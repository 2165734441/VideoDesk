package org.videodesk.mobile;
import android.media.*;
import java.io.*;
import java.nio.*;
import java.util.*;

/** Lossless AVC/AAC FLV remux. No video decoding or re-encoding. */
final class FlvMp4 {
 static int u24(byte[] b,int i){return ((b[i]&255)<<16)|((b[i+1]&255)<<8)|(b[i+2]&255);}
 static byte[] annex(byte[] b,int start,int length,int field)throws IOException{
  ByteArrayOutputStream out=new ByteArrayOutputStream(length+64);int end=start+length;
  while(start<end){if(start+field>end)throw new IOException("视频数据不完整");int n=0;for(int j=0;j<field;j++)n=(n<<8)|(b[start++]&255);if(n<=0||n>end-start)throw new IOException("视频片段长度无效");out.write(new byte[]{0,0,0,1});out.write(b,start,n);start+=n;}return out.toByteArray();
 }
 static final class Bits {
  byte[] b;int bit;Bits(byte[] src){ByteArrayOutputStream o=new ByteArrayOutputStream();int zeros=0;for(int i=1;i<src.length;i++){int v=src[i]&255;if(zeros>=2&&v==3){zeros=0;continue;}o.write(v);zeros=v==0?zeros+1:0;}b=o.toByteArray();}
  int get(int n)throws IOException{if(n<0||n>32||bit+n>b.length*8)throw new IOException("视频参数无效");int v=0;while(n-->0){v=(v<<1)|((b[bit/8]>>(7-bit%8))&1);bit++;}return v;}
  int ue()throws IOException{int n=0;while(get(1)==0){if(++n>30)throw new IOException("视频参数过长");}return (1<<n)-1+(n==0?0:get(n));}
  int se()throws IOException{int n=ue();return (n&1)==1?(n+1)/2:-n/2;}
 }
 static int[] dimensions(byte[] sps)throws IOException{
  Bits b=new Bits(sps);int profile=b.get(8);b.get(8);b.get(8);b.ue();int chroma=1,separate=0;
  if(Arrays.asList(100,110,122,244,44,83,86,118,128,138,139,134,135).contains(profile)){chroma=b.ue();if(chroma==3)separate=b.get(1);b.ue();b.ue();b.get(1);if(b.get(1)==1){int count=chroma!=3?8:12;for(int i=0;i<count;i++)if(b.get(1)==1){int last=8,next=8;for(int j=0;j<(i<6?16:64);j++){if(next!=0)next=(last+b.se()+256)%256;last=next==0?last:next;}}}}
  b.ue();int order=b.ue();if(order==0)b.ue();else if(order==1){b.get(1);b.se();b.se();int n=b.ue();if(n>256)throw new IOException("视频参数无效");for(int j=0;j<n;j++)b.se();}
  b.ue();b.get(1);int w=(b.ue()+1)*16,h=b.ue()+1,frame=b.get(1);h*=16*(2-frame);if(frame==0)b.get(1);b.get(1);
  if(b.get(1)==1){int l=b.ue(),r=b.ue(),t=b.ue(),bot=b.ue();int c=separate==1?0:chroma;int x=(c==1||c==2)?2:1,y=(c==1?2:1)*(2-frame);w-=(l+r)*x;h-=(t+bot)*y;}
  if(w<16||h<16||w>16384||h>16384)throw new IOException("视频尺寸无效");return new int[]{w,h};
 }
 static MediaFormat video(byte[] b)throws Exception{
  DataInputStream in=new DataInputStream(new ByteArrayInputStream(b,5,b.length-5));if(in.readUnsignedByte()!=1)throw new IOException("AVC 参数版本不支持");in.skipBytes(4);int count=in.readUnsignedByte()&31;byte[] sps=null;ByteArrayOutputStream csd=new ByteArrayOutputStream();
  for(int i=0;i<count;i++){int n=in.readUnsignedShort();byte[] v=new byte[n];in.readFully(v);if(sps==null)sps=v;csd.write(new byte[]{0,0,0,1});csd.write(v);}if(sps==null)throw new IOException("缺少视频 SPS 参数");byte[] first=csd.toByteArray();csd.reset();count=in.readUnsignedByte();for(int i=0;i<count;i++){int n=in.readUnsignedShort();byte[] v=new byte[n];in.readFully(v);csd.write(new byte[]{0,0,0,1});csd.write(v);}int[] d=dimensions(sps);MediaFormat f=MediaFormat.createVideoFormat("video/avc",d[0],d[1]);f.setByteBuffer("csd-0",ByteBuffer.wrap(first));f.setByteBuffer("csd-1",ByteBuffer.wrap(csd.toByteArray()));return f;
 }
 static long convert(File source,FileDescriptor fd)throws Exception{
  MediaMuxer mux=new MediaMuxer(fd,MediaMuxer.OutputFormat.MUXER_OUTPUT_MPEG_4);boolean started=false;long base=-1,last=0;int vt=-1,at=-1,lengthField=4;MediaFormat vf=null,af=null;boolean hasAudio=false,hasVideo=false;long videoSamples=0;
  try(DataInputStream in=new DataInputStream(new BufferedInputStream(new FileInputStream(source),262144))){byte[] header=new byte[9];in.readFully(header);if(header[0]!='F'||header[1]!='L'||header[2]!='V')throw new IOException("直播响应不是 FLV 视频流");hasAudio=(header[4]&4)!=0;hasVideo=(header[4]&1)!=0;if(!hasVideo)throw new IOException("直播没有视频轨道");int off=ByteBuffer.wrap(header,5,4).getInt();if(off<9||off>1024*1024)throw new IOException("FLV 头无效");VideoLibrary.skip(in,off-9);in.readInt();
   while(true){byte[] h=new byte[11];try{in.readFully(h);}catch(EOFException end){break;}int type=h[0]&31,size=u24(h,1);if(size>16*1024*1024)throw new IOException("直播数据块过大");long ts=(u24(h,4)|((long)(h[7]&255)<<24))*1000L;byte[] b=new byte[size];try{in.readFully(b);in.readInt();}catch(EOFException end){break;}
    if(type==9&&size>=5){if((b[0]&15)!=7)throw new IOException("该直播未提供 H.264 流，请选择其他清晰度或使用屏幕录制");if(b[1]==0){if(vf==null){vf=video(b);lengthField=(b[9]&3)+1;}continue;}}
    if(type==8&&size>=4){if(((b[0]&255)>>4)!=10)throw new IOException("该直播音频格式不支持手机直接录制");if(b[1]==0&&af==null){byte[] asc=Arrays.copyOfRange(b,2,b.length);int idx=((asc[0]&7)<<1)|((asc[1]&255)>>7),channels=(asc[1]>>3)&15;int[] freq={96000,88200,64000,48000,44100,32000,24000,22050,16000,12000,11025,8000,7350};if(idx>=freq.length||channels<1)throw new IOException("AAC 音频参数无效");af=MediaFormat.createAudioFormat("audio/mp4a-latm",freq[idx],channels);af.setByteBuffer("csd-0",ByteBuffer.wrap(asc));continue;}}
    if(!started){if(vf==null||(hasAudio&&af==null))continue;if(type!=9||size<5||b[1]!=1||((b[0]&255)>>4)!=1)continue;vt=mux.addTrack(vf);if(af!=null)at=mux.addTrack(af);mux.start();started=true;base=ts;}
    if(ts<base)continue;byte[] sample=null;int track=-1,flags=0;long pts=ts-base;
    if(type==9&&size>5&&b[1]==1){sample=annex(b,5,size-5,lengthField);track=vt;int composition=u24(b,2);if((composition&0x800000)!=0)composition-=0x1000000;pts+=composition*1000L;if(((b[0]&255)>>4)==1)flags=MediaCodec.BUFFER_FLAG_KEY_FRAME;videoSamples++;}
    else if(type==8&&size>2&&b[1]==1&&at>=0){sample=Arrays.copyOfRange(b,2,b.length);track=at;}
    if(sample!=null&&pts>=0){MediaCodec.BufferInfo info=new MediaCodec.BufferInfo();info.set(0,sample.length,pts,flags);mux.writeSampleData(track,ByteBuffer.wrap(sample),info);last=Math.max(last,pts);}
   }
   if(!started||videoSamples<2)throw new IOException("未收到足够视频数据，请确认主播正在直播");mux.stop();started=false;return last/1000;
  }finally{if(started)try{mux.stop();}catch(Exception ignored){}mux.release();}
 }
}
