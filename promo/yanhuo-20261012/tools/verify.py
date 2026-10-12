"""Decode and verify the deliverable; does not run application tests."""
from pathlib import Path
import subprocess,json,hashlib
root=Path(__file__).resolve().parents[1]
path=root/'yanhuo-ad-60s-1080p.mp4'
def run(args):
    r=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',errors='replace')
    assert r.returncode==0, r.stderr[-5000:]
    return r
meta=json.loads(run(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(path)]).stdout)
v=[s for s in meta['streams'] if s['codec_type']=='video']; a=[s for s in meta['streams'] if s['codec_type']=='audio']
assert len(v)==len(a)==1
v=v[0];a=a[0]
assert (v['codec_name'],v['width'],v['height'],v['r_frame_rate'],int(v['nb_frames']))==('h264',1920,1080,'30/1',1800)
assert abs(float(meta['format']['duration'])-60)<0.05
assert (a['codec_name'],a['sample_rate'],a['channels'])==('aac','48000',2)
frames=json.loads(run(['ffprobe','-v','error','-select_streams','v:0','-show_frames','-show_entries','frame=pix_fmt,color_range,color_space,color_primaries,color_transfer','-of','json',str(path)]).stdout)['frames']
assert len(frames)==1800, len(frames)
colors={tuple(f.get(k) for k in ['pix_fmt','color_range','color_space','color_primaries','color_transfer']) for f in frames}
assert colors=={('yuv420p','tv','bt709','bt709','bt709')},colors
r=run(['ffmpeg','-hide_banner','-v','error','-xerror','-i',str(path),'-f','null','-'])
assert not r.stderr.strip(),r.stderr
l=run(['ffmpeg','-hide_banner','-i',str(path),'-vn','-af','loudnorm=I=-18:TP=-1.5:LRA=8:print_format=json','-f','null','-']).stderr
start=l.rfind('{'); loud=json.JSONDecoder().raw_decode(l[start:])[0]
assert float(loud['input_tp'])<=-1.0,loud
report={'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),'duration_seconds':float(meta['format']['duration']),'width':v['width'],'height':v['height'],'fps':v['r_frame_rate'],'decoded_video_frames':len(frames),'video_codec':v['codec_name'],'color_parameters':list(next(iter(colors))),'audio_codec':a['codec_name'],'audio_sample_rate':int(a['sample_rate']),'audio_channels':a['channels'],'integrated_loudness_lufs':float(loud['input_i']),'true_peak_dbtp':float(loud['input_tp']),'decode_check':'passed; full video and audio decoded, no FFmpeg errors','narration':'none; sole mapped audio input is original-bed.wav','application_tests':'not run; artifact-only change','production_deployment':'not performed'}
(root/'verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(report,ensure_ascii=False,indent=2))
