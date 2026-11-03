import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from fastapi import HTTPException
from PIL import Image
from integrations.ai_package import save_text,write_reading,combine,build,UPLOAD,EXTRA
from integrations.material_history import History,title_name


class Packages(unittest.TestCase):
    def test_exact_pixel_ocr_cache_survives_new_job_id(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);data=root/'data';data.mkdir();jobs={}
            def ffmpeg(jid,args,private=False):Image.new('RGB',(40,30),'white').save(Path(args[-1]).with_name('0000000000000000.png'))
            a=SimpleNamespace(DATA=data,job_by_id=lambda j:jobs[j],change=lambda *args,**kwargs:None,run_ffmpeg=ffmpeg,check_cancel=lambda j:None)
            with patch('integrations.ai_package.OcrSession') as session:
                session.return_value.__enter__.return_value.threads=2
                session.return_value.__enter__.return_value.read.return_value={'text':['价格199'],'boxes':[],'confidence':[.99]}
                for jid in ('first','retry'):
                    base=root/jid;base.mkdir();work=root/(jid+'-work');work.mkdir();jobs[jid]={'directory':str(base),'title':jid}
                    build(a,jid,'source',{'hasAudio':False},[],{}, {'cells':[]},work,{'screen_text':True},0,1)
                    self.assertIn('价格199',(base/UPLOAD/'01_完整内容_001.txt').read_text('utf-8'))
                self.assertEqual(session.return_value.__enter__.return_value.read.call_count,1)

    def test_full_text_is_split_without_losing_one_character(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);text='原价1299，现价199！\n'+('长台词中文\n'*20000)+'末尾不能丢'
            names=save_text(root,[text],limit=1000)
            self.assertGreater(len(names),1)
            self.assertEqual(''.join((root/n).read_text('utf-8') for n in names),text)

    def test_joint_order_titles_clocks_all_images_and_all_words(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);items=[]
            for i,title in enumerate(['商品.版本2.mp4','第二段.mp4']):
                folder=root/str(i);(folder/UPLOAD).mkdir(parents=True);(folder/EXTRA).mkdir()
                Image.new('RGB',(20,30),'red').save(folder/UPLOAD/'sheet.png')
                data={'title':title,'from':2,'to':7,'rows':[{'from':3,'to':4,'text':f'完整台词{i}价格{199+i}'}],
                    'visuals':[{'id':'F00001','at':3,'sheet':'sheet.png','text':[f'数字{1299+i}']}],
                    'timeline':[[3,'F00001']],'images':['sheet.png'],'warnings':[],'metrics':{}}
                (folder/EXTRA/'联合内容源.json').write_text(json.dumps(data),encoding='utf-8');items.append((title,folder))
            result=combine(root,items);upload=root/'联合AI分析包'/UPLOAD
            texts='\n'.join((upload/n).read_text('utf-8') for n in result['files'] if n.endswith('.txt'))
            self.assertLess(texts.index('V01 标题：商品'),texts.index('V02 标题：第二'))
            self.assertIn('V02 联合 6.000–7.000；原片 3.000–4.000',texts)
            for t in ['完整台词0价格199','完整台词1价格200','数字1299','数字1300']:self.assertIn(t,texts)
            self.assertTrue((upload/'V01_sheet.png').exists());self.assertTrue((upload/'V02_sheet.png').exists())
            self.assertTrue(all((upload/n).exists() for n in result['files']))
            self.assertNotIn('资料索引.json',result['files'])

    def test_missing_required_image_never_reports_a_complete_pack(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError,'不完整'):write_reading(Path(tmp),'x',['台词'],['missing.png'],[])

    def test_missing_video_in_joint_is_explicit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);result=combine(root,[('不能读取的视频',root/'gone')])
            self.assertEqual(result['quality'],'需要补充')
            self.assertIn('不能读取的视频',''.join(result['warnings']))

    def test_separate_audio_is_not_mistaken_for_a_silent_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            base=Path(tmp);work=base/'work';work.mkdir()
            job={'directory':str(base),'title':'独立音轨'}
            def ffmpeg(jid,args,private=False):Image.new('RGB',(40,30),'white').save(Path(args[-1]).with_name('0000000000000000.png'))
            a=SimpleNamespace(job_by_id=lambda j:job,change=lambda *args,**kwargs:None,run_ffmpeg=ffmpeg,check_cancel=lambda j:None)
            result=build(a,'job','video',{'hasAudio':False,'source_has_audio':True},[],{}, {'cells':[]},work,{'screen_text':False},0,1)
            self.assertTrue(result['metrics']['has_audio']);self.assertTrue(any('有音轨但没有' in w for w in result['warnings']))
            self.assertFalse(any('原视频无音轨' in w for w in result['warnings']))


class HistorySafety(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.data=self.root/'data';self.data.mkdir()
        self.jobs={}
        def load(p,f):return json.loads(p.read_text('utf-8')) if p.exists() else f
        def change(jid,**v):self.jobs[jid].update(v)
        def refresh(jid):
            base=Path(self.jobs[jid]['directory']);self.jobs[jid]['files']=[{'name':p.relative_to(base).as_posix()} for p in base.rglob('*') if p.is_file()]
        self.a=SimpleNamespace(DATA=self.data,JOBS=self.jobs,load_json=load,atomic_json=lambda p,v:p.write_text(json.dumps(v),encoding='utf-8'),
            change=change,job_by_id=lambda jid:self.jobs[jid],refresh_files=refresh,check_cancel=lambda jid:None)
        self.h=History(self.a)
    def tearDown(self):self.tmp.cleanup()
    def job(self,jid,title='成品'):
        base=self.root/jid;base.mkdir();job={'id':jid,'directory':str(base),'source':str(self.root/'original.mp4'),'status':'done','title':title,'files':[]}
        self.jobs[jid]=job;return base
    def record(self,jid):self.a.refresh_files(jid);self.h.record(jid)
    def test_titles_preserve_dots_unicode_and_windows_reserved_names(self):
        self.assertEqual(title_name('商品.版本2.mp4'),'商品.版本2');self.assertEqual(title_name('PPO-GRPO（上）'),'PPO-GRPO（上）')
        self.assertEqual(title_name('CON.mp4'),'_CON');self.assertEqual(title_name('a:b?c.mov'),'a_b_c')
    def test_cleanup_skips_moved_modified_inputs_and_unrecorded_files(self):
        base=self.job('old');(base/'same.txt').write_text('same');(base/'modified.txt').write_text('old');(base/'moved.txt').write_text('move');(base/'video.mp4').write_bytes(b'original')
        self.record('old');(base/'modified.txt').write_text('edited');(base/'moved.txt').rename(self.root/'moved.txt');(base/'personal.txt').write_text('mine')
        result=self.h.clean('old');self.assertEqual(result['deleted'],1);self.assertEqual(len(result['skipped']),2)
        self.assertEqual((base/'modified.txt').read_text(),'edited');self.assertTrue((base/'video.mp4').exists());self.assertTrue((base/'personal.txt').exists());self.assertTrue((self.root/'moved.txt').exists())
    def test_a_file_later_registered_as_input_is_protected(self):
        base=self.job('old');source=base/'original.png';source.write_bytes(b'input');self.record('old')
        self.jobs['input']={'source':str(source)}
        self.assertEqual(self.h.clean('old')['deleted'],0);self.assertTrue(source.exists())
    def test_active_cleanup_rejected(self):
        self.job('active');self.jobs['active']['status']='running'
        with self.assertRaises(HTTPException):self.h.preview('active')
    def test_regenerate_replaces_old_directory_and_preserves_original(self):
        original=self.root/'original.mp4';original.write_bytes(b'unchanged')
        old=self.job('old');new=self.job('new');(old/'old.txt').write_text('old');(new/'new.txt').write_text('new')
        self.record('old');self.record('new');note=self.h.replace('old','new')
        self.assertIn('已覆盖',note);self.assertTrue((old/'new.txt').exists());self.assertFalse((old/'old.txt').exists())
        self.assertEqual(self.jobs['new']['directory'],str(old));self.assertEqual(self.jobs['old']['superseded_by'],'new');self.assertEqual(original.read_bytes(),b'unchanged')
        self.assertFalse(new.exists())
        preview=self.h.preview('old');self.assertIn('old.txt',preview['files'])
        self.h.clean('old');self.assertTrue((old/'new.txt').exists());self.assertEqual(original.read_bytes(),b'unchanged')
    def test_regenerate_refuses_to_move_added_files_or_original_video(self):
        old=self.job('old');new=self.job('new');(old/'old.txt').write_text('old');(new/'new.txt').write_text('new');self.record('old');self.record('new')
        (old/'personal.txt').write_text('user');note=self.h.replace('old','new')
        self.assertIn('另存',note);self.assertTrue((old/'old.txt').exists());self.assertTrue((old/'personal.txt').exists());self.assertTrue((new/'new.txt').exists())


if __name__=='__main__':unittest.main()
