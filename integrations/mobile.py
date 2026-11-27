"""USB-paired Android companion; the bridge never listens on the LAN.

The phone always talks to 127.0.0.1:47631 and the desktop forwards that port
over ADB, so the same bridge can run over the cable or over Wi-Fi debugging.
Wi-Fi is preferred whenever it answers: pulling the cable then changes nothing
for detection, recording or replay transfer."""
import hmac,json,os,re,secrets,shutil,subprocess,threading,time,uuid
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from fastapi import HTTPException
from pydantic import BaseModel
from . import live,phone_import
from .storage import atomic_json


def normalize_video_url(text):
    """Accept only a concrete Douyin/Bilibili video link from the phone.

    Share sheets often send a short host (v.douyin.com / b23.tv); those are
    valid too, but profile links, live rooms and search pages are not videos.
    """
    import re
    from urllib.parse import urlparse
    match = re.search(r'https?://[^\s<>"\']+', text or '')
    if not match:
        raise ValueError('请粘贴抖音或 B 站视频链接')
    url = match.group().rstrip('.,;!）)]')
    parsed = urlparse(url)
    host = (parsed.hostname or '').lower()
    path = parsed.path or '/'
    if host in ('v.douyin.com', 'www.iesdouyin.com'):
        if not re.fullmatch(r'/[A-Za-z0-9_-]{4,80}/?', path):
            raise ValueError('抖音分享短链不完整')
        return url
    if host in ('www.douyin.com', 'douyin.com', 'm.douyin.com'):
        if not re.fullmatch(r'/video/\d{5,30}/?', path):
            raise ValueError('请使用抖音视频详情链接（/video/数字）')
        return url
    if host == 'b23.tv':
        if not re.fullmatch(r'/[A-Za-z0-9]{4,30}/?', path):
            raise ValueError('B 站分享短链不完整')
        return url
    if host in ('www.bilibili.com', 'bilibili.com', 'm.bilibili.com'):
        if not re.fullmatch(r'/video/(BV[0-9A-Za-z]{10}|av\d{1,15})/?', path):
            raise ValueError('请使用 B 站视频详情链接（/video/BV… 或 av…）')
        return url
    raise ValueError('目前只支持抖音或 B 站视频链接')


class Decision(BaseModel):
    event_id:str
    target:str


class DeviceRequest(BaseModel):
    serial:str=''

class PhoneRecord(BaseModel):
    anchor_id:str
    action:str='record'


class Companion:
    def __init__(self,a):
        self.a=a;self.lock=threading.RLock();self.events=[];self.commands=[];self.last_seen=0;self.phone_status={};self.server=None
        secret=a.DATA/'credentials/mobile-pair.json';saved=a.load_json(secret,{})
        secret.parent.mkdir(parents=True,exist_ok=True)
        self.key=saved.get('key') or secrets.token_urlsafe(32)
        self.paired_serial=str(saved.get('serial') or '')
        self.wireless=str(saved.get('wireless') or '')
        self.reverse_on=''
        self.link_lock=threading.RLock()
        self.stopping=threading.Event();self.imports=phone_import.Imports(a)
        self.wake_at=0.0
        self.listener_at=0.0
        if not saved:self.save_pair()

    def save_pair(self):
        atomic_json(self.a.DATA/'credentials/mobile-pair.json',
                    {'key':self.key,'serial':self.paired_serial,'wireless':self.wireless})

    def concurrency(self):
        """Phone capacity follows the live settings when available."""
        try:
            value=int(getattr(getattr(self.a,'LIVE',None),'settings',{}).get('concurrency',8))
        except (TypeError,ValueError):
            value=8
        return max(1,min(value,8))

    def tool(self):
        configured=self.a.load_json(self.a.DATA/'phone-settings.json',{}).get('adb','')
        choices=[Path(configured)] if configured else []
        if shutil.which('adb'):choices.append(Path(shutil.which('adb')))
        homes=[Path.home(),Path(os.environ.get('USERPROFILE') or Path.home())]
        for home in homes:
            root=home/'Documents/scrcpy'
            if root.is_dir():choices.extend(root.glob('**/adb.exe'))
            sdk=home/'AppData/Local/Android/Sdk/platform-tools/adb.exe'
            choices.append(sdk)
        local=os.environ.get('LOCALAPPDATA')
        if local:choices.extend([Path(local)/'Android/Sdk/platform-tools/adb.exe',Path(local)/'scrcpy/adb.exe'])
        # Launchers may redirect HOME/USERPROFILE; the real tools can still live
        # under another Windows profile on this machine.
        for base in list(Path('C:/Users').glob('*/AppData/Local/Android/Sdk/platform-tools/adb.exe'))+list(Path('C:/Users').glob('*/Documents/scrcpy/**/adb.exe')):
            choices.append(base)
        for candidate in choices:
            if candidate.is_file() and candidate.name.lower() in ('adb.exe','adb'):return str(candidate)
        raise HTTPException(400,'未找到 ADB。请安装手机投屏工具 scrcpy，或在设置里指定 adb 路径')

    def adb(self,*args,timeout=25):
        p=subprocess.run([self.tool(),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        if p.returncode:raise HTTPException(400,'手机连接操作未完成：'+(p.stderr or p.stdout)[-300:])
        return p.stdout

    def reachable(self,serial):
        """True when that transport still answers; used to pick USB or Wi-Fi."""
        if not serial:return False
        try:return (self.adb('-s',serial,'get-state',timeout=10) or '').strip()=='device'
        except Exception:return False

    def wifi_ip(self,serial):
        try:out=self.adb('-s',serial,'shell','ip','-f','inet','addr','show','wlan0',timeout=12)
        except Exception:return ''
        match=re.search(r'inet\s+(\d{1,3}(?:\.\d{1,3}){3})',str(out or ''))
        return match.group(1) if match else ''

    def mdns_wireless(self,serial):
        """Find the phone's current Wi-Fi debugging address by device name, so a
        fresh DHCP lease does not require the cable again."""
        if not serial:return ''
        try:rows=self.adb('mdns','services',timeout=12)
        except Exception:return ''
        for row in str(rows or '').splitlines():
            parts=row.split()
            if len(parts)<3 or parts[1]!='_adb._tcp':continue
            if serial not in parts[0]:continue
            if ':' in parts[2]:return parts[2]
        return ''

    def wifi_address(self):
        """The Wi-Fi address that answers right now; rediscovers a new lease."""
        if self.wireless and self.reachable(self.wireless):return self.wireless
        discovered=self.mdns_wireless(self.paired_serial)
        if discovered and self.reachable(discovered):
            if discovered!=self.wireless:
                self.wireless=discovered;self.save_pair()
            return discovered
        return ''

    def preferred(self):
        # Wi-Fi wins while it answers: that is the transport that survives an
        # unplugged cable, so switching later would leave a gap.
        if self.wireless and self.reachable(self.wireless):return self.wireless
        if self.paired_serial and self.reachable(self.paired_serial):return self.paired_serial
        return ''

    def apply_reverse(self,serial=None):
        with self.link_lock:
            return self.apply_reverse_locked(serial)

    def apply_reverse_locked(self,serial=None):
        if not self.server:return False
        serial=serial or self.preferred()
        if not serial or not self.reachable(serial):return False
        target='tcp:'+str(self.server.server_port)
        try:self.adb('-s',serial,'reverse','--remove-all',timeout=15)
        except Exception:pass
        self.adb('-s',serial,'reverse','tcp:47631',target,timeout=15)
        self.reverse_on=serial
        return True

    def enable_wireless(self,serial=''):
        with self.link_lock:
            return self.enable_wireless_locked(serial)

    def enable_wireless_locked(self,serial=''):
        self.start()
        if not self.server:raise HTTPException(400,'手机连接服务尚未就绪')
        usb=serial or self.paired_serial
        known=[d['serial'] for d in self.devices() if not d.get('wireless') and d['state']=='device']
        if usb not in known:
            if len(known)!=1:raise HTTPException(400,'请先用数据线连接手机（只需一次），再开启无线录制')
            usb=known[0]
        ip=self.wifi_ip(usb)
        if not ip:raise HTTPException(400,'手机没有连上 Wi-Fi，请先连上和电脑同一个 Wi-Fi')
        try:self.adb('-s',usb,'tcpip','5555',timeout=25)
        except HTTPException as exc:raise HTTPException(400,'手机无线调试开启失败：'+str(exc.detail)[-160:])
        address=ip+':5555';found=''
        for _ in range(12):
            if self.reachable(address):found=address;break
            discovered=self.mdns_wireless(usb)
            if discovered and discovered!=address and self.reachable(discovered):found=discovered;break
            try:self.adb('connect',address,timeout=12)
            except Exception:pass
            time.sleep(1.5)
        if not found:raise HTTPException(400,'无线调试未接通：请确认手机与电脑在同一 Wi-Fi，且路由器未开启设备隔离')
        self.wireless=found;self.save_pair()
        try:self.apply_reverse(found)
        except HTTPException:pass
        return {'message':'无线已接通（'+found+'）。现在拔掉数据线，手机仍会自己录到手机'}

    def devices(self):
        raw=self.adb('devices','-l')
        usb,wireless=[],[]
        for row in raw.splitlines()[1:]:
            fields=row.split()
            if len(fields)<2:continue
            item={'serial':fields[0],'state':fields[1],'wireless':':' in fields[0],
                  'model':next((v[6:] for v in fields[2:] if v.startswith('model:')),'Android')}
            (wireless if item['wireless'] else usb).append(item)
        if not wireless:return usb
        # One phone can answer over both transports; showing both would make the
        # pairing panel ask which device to use.
        return usb or [next((d for d in wireless if d['serial']==self.wireless),wireless[0])]

    def start(self):
        with self.lock:
            if self.server:return
            owner=self
            class Handler(BaseHTTPRequestHandler):
                def log_message(self,*args):pass
                def send(self,code,value):
                    body=json.dumps(value,ensure_ascii=False).encode();self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
                def reject(self,code,value,size=0):
                    # Closing with an unread upload can reset the TCP socket on
                    # Windows before the client receives the error. Flush the
                    # response, then drain a bounded amount with a short deadline.
                    self.close_connection=True
                    self.send(code,value);self.wfile.flush()
                    remaining=min(max(0,size),phone_import.CHUNK+65536)
                    deadline=time.monotonic()+1
                    try:
                        while remaining and time.monotonic()<deadline:
                            self.connection.settimeout(max(.01,deadline-time.monotonic()))
                            block=self.rfile.read1(min(remaining,65536))
                            if not block:break
                            remaining-=len(block)
                    except OSError:pass
                def handle_request(self):
                    self.connection.settimeout(35)
                    try:
                        size=int(self.headers.get('Content-Length','0'))
                        if not hmac.compare_digest(self.headers.get('X-Phone-Key',''),owner.key):self.reject(401,{'error':'需要重新通过 USB 配对'},size);return
                        if self.command=='POST' and self.path.startswith('/v1/recordings/chunk/'):
                            if size<=0 or size>phone_import.CHUNK:self.reject(413,{'error':'传输块大小必须在 1 字节至 4 MiB 之间'},size);return
                            rid=self.path.rsplit('/',1)[-1];offset=int(self.headers.get('X-Offset','-1'))
                            block=self.rfile.read(size)
                            if len(block)!=size:self.send(400,{'error':'传输中断'});return
                            self.send(200,owner.imports.chunk(rid,offset,block));return
                        if size<0 or size>65536:self.reject(413,{'error':'请求过大'},size);return
                        value=json.loads(self.rfile.read(size)) if size else {}
                        with owner.lock:owner.last_seen=time.time()
                        if self.command=='GET' and self.path=='/v1/config':
                            with owner.lock:
                                owner.commands=[c for c in owner.commands if time.time()-c['time']<180]
                                commands=list(owner.commands)
                            self.send(200,{'anchors':owner.a.LIVE.state()['anchors'],'commands':commands,'concurrency':owner.concurrency()});return
                        if self.command=='POST' and self.path=='/v1/recordings/begin':self.send(200,owner.imports.begin(value));return
                        if self.command=='POST' and self.path=='/v1/recordings/complete':self.send(200,owner.imports.complete(str(value.get('id',''))));return
                        # A video link pasted/shared on the phone becomes a normal
                        # desktop download job.  The phone only sends the link;
                        # cookies, output paths and media workers stay on the PC.
                        if self.command=='POST' and self.path=='/v1/video':
                            url=normalize_video_url(str(value.get('url','')))
                            title=str(value.get('title','') or '手机分享视频').strip()[:120]
                            from . import collect
                            jobs=collect.queue_direct_videos(owner.a,[collect.Picked(url=url,title=title)],'')
                            self.send(200,{'ok':True,'jobs':jobs,'url':url});return
                        if self.command=='POST' and self.path=='/v1/events':self.send(200,owner.event(value));return
                        if self.command=='POST' and self.path=='/v1/status':
                            with owner.lock:
                                owner.phone_status={k:value.get(k) for k in ('monitoring','recording','message','active','capabilities','transfers')}
                                ack=value.get('ack')
                                if ack:owner.commands=[c for c in owner.commands if c['id']!=ack]
                            self.send(200,{'ok':True});return
                        if self.command=='POST' and self.path=='/v1/anchors':
                            req=live.Anchor(**value);self.send(200,owner.a.LIVE.put(req));return
                        # The phone is a remote control too: it can start or stop
                        # a desktop recording without walking back to the PC.
                        if self.command=='POST' and self.path.startswith('/v1/anchors/'):
                            parts=[p for p in self.path.split('/') if p]
                            if len(parts)==4 and parts[3] in ('start','stop'):
                                self.send(200,getattr(owner.a.LIVE,parts[3])(parts[2]));return
                            self.send(404,{'error':'接口不存在'});return
                        self.send(404,{'error':'接口不存在'})
                    except HTTPException as exc:self.send(exc.status_code,{'error':str(exc.detail)})
                    except (ValueError,TypeError):self.send(400,{'error':'手机请求格式无效'})
                    except Exception:self.send(500,{'error':'电脑处理手机请求失败，请在电脑检查任务'})
                do_GET=handle_request
                do_POST=handle_request
            self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
            threading.Thread(target=self.server.serve_forever,daemon=True,name='phone-usb').start()
            threading.Thread(target=self.reconnect_loop,daemon=True,name='phone-reconnect').start()
            threading.Thread(target=self.initial_link,daemon=True,name='phone-first-link').start()

    def initial_link(self):
        # A restart picks a new local port, so publish it straight away instead
        # of waiting for the phone to look offline first.
        self.stopping.wait(1)
        try:self.apply_reverse()
        except Exception:pass
        # A paired phone that never went wireless gets one quiet attempt per
        # launch, so "plug in once, then unplug" needs no extra click. It is
        # skipped as soon as an address was learned and saved.
        if self.paired_serial and not self.wireless:
            try:self.enable_wireless(self.paired_serial)
            except Exception:pass

    def keep_link(self):
        """Move the forward to Wi-Fi the moment the cable disappears.

        Waiting for the phone to go quiet first would pause detection for half a
        minute; the USB transport vanishing is already proof enough.
        """
        if not self.server:return False
        if self.reverse_on and ':' not in self.reverse_on and self.paired_serial:
            if not self.reachable(self.paired_serial):
                address=self.wifi_address()
                if address:
                    try:return self.apply_reverse(address)
                    except Exception:return False
        restored=self.reconnect()
        if self.last_seen and time.time()-self.last_seen>90:self.ensure_listener()
        self.revive()
        return restored
    def ensure_listener(self,serial=None):
        """Re-bind the notification listener after the companion is updated.

        Installing a new APK leaves the listener binding stale: the permission
        is still listed, yet the system never connects the service, so the
        background heartbeat stops while the app looks perfectly installed.
        Asking the system to allow the listener again re-establishes it.
        """
        if time.time()-self.listener_at<300:return False
        serial=serial or self.preferred()
        if not serial:return False
        self.listener_at=time.time()
        try:
            self.adb('-s',serial,'shell','cmd','notification','allow_listener',
                     'org.videodesk.mobile/org.videodesk.mobile.LiveNoticeService',timeout=20)
        except Exception:return False
        return True
    def screen_off(self,serial):
        try:out=str(self.adb('-s',serial,'shell','dumpsys','power',timeout=12) or '')
        except Exception:return False
        match=re.search(r'mWakefulness=(\w+)',out)
        return bool(match) and match.group(1) in ('Asleep','Dozing')
    def revive(self,force=False):
        """Wake a frozen companion so queued live-start commands still land.

        Android parks background activities after a while; the phone stays
        paired but stops polling, which silently loses the opening minutes of a
        stream. Bringing the activity back is harmless while the screen is off,
        so only a long silence justifies interrupting a phone that is in use.
        ``force`` skips those politeness checks: a live just went online, and a
        command nobody reads is worse than a screen lighting up.
        """
        if not self.paired_serial and not self.last_seen:return False
        if not force and self.last_seen and time.time()-self.last_seen<30:return False
        serial=self.preferred()
        if not serial:return False
        if not force and self.last_seen and time.time()-self.last_seen<180 and not self.screen_off(serial):return False
        if time.time()-self.wake_at<(10 if force else 45):return False
        self.wake_at=time.time()
        try:self.adb('-s',serial,'shell','am','start','-n','org.videodesk.mobile/.MainActivity',timeout=20)
        except Exception:return False
        return True

    def reconnect(self):
        # Restore only a device explicitly paired before. Do not launch an app,
        # install anything or request screen capture during background recovery.
        if not self.server or time.time()-self.last_seen<20:return False
        # The phone went quiet. Wi-Fi is the path that keeps working after the
        # cable is pulled, so restore it first and only then fall back to USB.
        order=[self.wireless,self.mdns_wireless(self.paired_serial),self.paired_serial]
        for address in order:
            if not address:continue
            if not self.reachable(address):
                if ':' not in address:continue
                try:self.adb('connect',address,timeout=12)
                except Exception:pass
                if not self.reachable(address):continue
            if ':' in address and address!=self.wireless:
                self.wireless=address;self.save_pair()
            try:
                if self.apply_reverse(address):return True
            except Exception:continue
        return False

    def reconnect_loop(self):
        while not self.stopping.is_set():
            try:self.keep_link()
            except Exception:pass
            self.stopping.wait(6)

    def wireless_after_pair(self,serial):
        # Switching adbd to TCP restarts the daemon, so it must not run in the
        # same breath as the pairing intent.
        self.stopping.wait(4)
        try:self.enable_wireless(serial)
        except Exception:pass

    def install_package(self,serial,apk):
        """Install the companion while auto-pressing vivo's confirmation page.

        vivo routes every sideload through its store page and parks the adb
        session on a "继续安装" button, so an unattended install used to hang for
        the whole timeout and then fail. A helper thread presses that button as
        soon as it appears and keeps the store process from taking the page
        over, which is exactly what a person would do by hand.
        """
        stop=threading.Event()
        def guard():
            while not stop.is_set():
                try:self.adb('-s',serial,'shell','am force-stop com.bbk.appstore',timeout=6)
                except Exception:pass
                try:
                    xml=self.adb('-s',serial,'shell',
                        'uiautomator dump /sdcard/vd-install.xml >/dev/null 2>&1; cat /sdcard/vd-install.xml',timeout=15)
                    for match in re.finditer(r'text="(?:继续安装|安装)"[^>]*bounds="\[(\d+),(\d+)\]\[(\d+),(\d+)\]"',xml or ''):
                        x=(int(match.group(1))+int(match.group(3)))//2
                        y=(int(match.group(2))+int(match.group(4)))//2
                        self.adb('-s',serial,'shell','input','tap',str(x),str(y),timeout=8)
                        break
                except Exception:pass
                stop.wait(1.2)
        helper=threading.Thread(target=guard,daemon=True,name='phone-install-guard')
        helper.start()
        try:return self.adb('-s',serial,'install','-r',str(apk),timeout=180)
        finally:stop.set();helper.join(timeout=3)

    def pair(self,serial='',install=False):
        self.start();devices=[d for d in self.devices() if d['state']=='device']
        if not serial:
            if len(devices)!=1:raise HTTPException(400,'请只连接一台手机，或先选择设备')
            serial=devices[0]['serial']
        if serial not in [d['serial'] for d in devices]:raise HTTPException(400,'所选手机未连接或尚未允许 USB 调试')
        if install:
            apk=self.a.ROOT/'mobile/VideoDesk-Mobile.apk'
            if not apk.is_file():raise HTTPException(400,'手机安装包缺失，请重新获取完整版本')
            result=self.install_package(serial,apk)
            if 'Success' not in result:raise HTTPException(400,'手机应用安装未完成，请查看手机提示')
        self.adb('-s',serial,'reverse','tcp:47631','tcp:'+str(self.server.server_port))
        self.reverse_on=serial
        # Pairing credentials are never printed or included in task evidence.
        self.adb('-s',serial,'shell','am','start','-n','org.videodesk.mobile/.MainActivity','--es','endpoint','http://127.0.0.1:47631','--es','pairing_key',self.key)
        self.paired_serial=serial
        self.save_pair()
        threading.Thread(target=self.wireless_after_pair,args=(serial,),daemon=True,name='phone-wireless').start()
        return {'message':'已连接手机伴侣。正在开启无线通道，接通后拔掉数据线也会继续录'}

    def event(self,value):
        key=str(value.get('anchor_id',''));state=value.get('state')
        if state not in ('online','offline','unknown'):raise HTTPException(400,'未知开播状态')
        with self.a.LIVE.lock:
            anchor=self.a.LIVE.anchors.get(key)
            if not anchor:raise HTTPException(404,'主播已删除，请同步列表')
            anchor=dict(anchor)
        with self.lock:
            previous=next((e for e in reversed(self.events) if e['anchor_id']==key),None)
            if previous and previous['state']==state and time.time()-previous['time']<300:return {'ok':True,'duplicate':True}
            entry={'id':uuid.uuid4().hex[:12],'anchor_id':key,'name':anchor['name'] or value.get('name') or '主播',
                   'state':state,'time':time.time(),'decision':'pending' if state=='online' else '',
                   'message':str(value.get('message',''))[:200]}
            self.events.append(entry);self.events=self.events[-100:]
        if state=='offline':
            with self.lock:
                for event in self.events:
                    if event['anchor_id']==key and event['decision']=='pending':event['decision']='offline'
                self.commands=[c for c in self.commands if c['anchor_id']!=key]
        if state=='online' and anchor['enabled'] and live.in_window(anchor.get('windows',[])) and not value.get('require_confirmation'):
            target=anchor.get('record_target','ask')
            if target in ('pc','phone'):return self.decide(entry['id'],target)
        return {'ok':True,'event_id':entry['id']}

    def decide(self,eid,target):
        if target not in ('pc','phone','dismiss'):raise HTTPException(400,'请选择电脑录制、手机录制或忽略')
        with self.lock:
            event=next((e for e in self.events if e['id']==eid),None)
            if not event:raise HTTPException(404,'开播通知不存在')
            if event['state']!='online' or time.time()-event['time']>600:raise HTTPException(409,'通知已过期，请重新检测')
            if event['decision']!='pending':return {'ok':True,'decision':event['decision']}
            # Reserve before starting work; concurrent clicks cannot create duplicates.
            event['decision']='starting'
            try:
                result=self.a.LIVE.start(event['anchor_id']) if target=='pc' else {}
                if target=='phone':result=self.request_record(event['anchor_id'])
                event['decision']=target
                return {'ok':True,'decision':target,**result}
            except Exception:event['decision']='pending';raise

    def request_record(self,key,action='record',info=None):
        if action not in ('record','stop'):raise HTTPException(400,'未知手机录制操作')
        with self.a.LIVE.lock:
            if key not in self.a.LIVE.anchors:raise HTTPException(404,'主播不存在')
        with self.lock:
            # Only a phone that was never paired is refused. A paired phone may
            # simply be backgrounded after a desktop restart, so queue the
            # command and wake it; commands live 180 seconds.
            if not self.last_seen and not self.paired_serial:raise HTTPException(409,'手机伴侣尚未连接，请先连接手机并打开手机软件')
            if not self.last_seen or time.time()-self.last_seen>=25:self.revive(force=True)
            if not any(c['anchor_id']==key and c['type']==action and time.time()-c['time']<180 for c in self.commands):
                command={'id':uuid.uuid4().hex[:12],'type':action,'anchor_id':key,'time':time.time()}
                # A resolved stream lets the phone record without its own
                # WebView resolver, which is the fragile part on mobile.
                if action=='record' and isinstance(info,dict) and info.get('url'):
                    command['url']=info['url']
                    command['room_id']=str(info.get('room_id') or '')
                    command['agent']=str(info.get('user_agent') or '')
                    command['cookie']='; '.join(c['name']+'='+c['value'] for c in info.get('cookies') or [])
                self.commands.append(command)
        return {'message':'已发送到手机，手机可同时录制最多 %d 位主播' % self.concurrency()}

    def state(self):
        try:devices=self.devices();error=''
        except Exception:devices=[];error='未检测到手机连接工具，请先运行 scrcpy 并允许 USB 调试'
        with self.lock:
            for event in self.events:
                if event['decision']=='pending' and time.time()-event['time']>600:event['decision']='expired'
            return {'devices':devices,'connected':time.time()-self.last_seen<25,'status':dict(self.phone_status),
                               'wireless':{'address':self.wireless,'on_wifi':bool(self.reverse_on and ':' in self.reverse_on)},
                               'imports':self.imports.state(),'events':[dict(e) for e in reversed(self.events[-30:])],'message':error}

    def close(self):
        self.stopping.set()
        if self.server:self.server.shutdown();self.server.server_close();self.server=None


def install(app,a):
    manager=Companion(a)
    @app.on_event('startup')
    def start():manager.start()
    @app.on_event('shutdown')
    def stop():manager.close()
    @app.get('/api/phone')
    def state():return manager.state()
    @app.post('/api/phone/pair')
    def pair(req:DeviceRequest):return manager.pair(req.serial)
    @app.post('/api/phone/install')
    def setup(req:DeviceRequest):return manager.pair(req.serial,install=True)
    @app.post('/api/phone/wireless')
    def wireless(req:DeviceRequest):return manager.enable_wireless(req.serial)
    @app.post('/api/phone/decision')
    def decision(req:Decision):return manager.decide(req.event_id,req.target)
    @app.post('/api/phone/record')
    def record(req:PhoneRecord):return manager.request_record(req.anchor_id,req.action,a.LIVE.recent_info(req.anchor_id))
    return manager
