"""Render the 60-second, narration-free promo with FFmpeg. No application changes.

Run: python promo/yanhuo-20261012/tools/render.py [--download] [--preview]
Stock originals are private working inputs in .runtime/promo-stock (gitignored).
Requires FFmpeg and FFprobe on PATH; only Python standard library is used.
"""
from pathlib import Path
import argparse
import hashlib
import json
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[1]
STOCK = REPO / '.runtime' / 'promo-stock'
WORK = ROOT / 'work'
FPS = 30
COLOR_PIPELINE_VERSION = 2
GRADES = {
    'none': '',
    'food': 'eq=contrast=1.04:saturation=1.02:gamma=0.99,colorbalance=rs=0.008:bs=-0.008',
    'wok': 'eq=contrast=1.06:saturation=1.08:gamma=1.12,colorbalance=rs=0.018:bs=-0.014',
    'kitchen': 'eq=contrast=1.05:saturation=1.04:gamma=1.05,colorbalance=rs=0.024:bs=-0.024',
    'drink': 'eq=contrast=1.03:saturation=0.92:gamma=0.99',
    'outdoor': 'eq=contrast=1.04:saturation=0.85:gamma=0.96,colorbalance=rs=0.013:bs=-0.009',
    'night': 'eq=contrast=1.03:saturation=0.88:gamma=1.08',
}


def run(args):
    r = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding='utf-8', errors='replace')
    if r.returncode:
        raise RuntimeError('Command failed: ' + ' '.join(args[:5]) + '\n' + r.stderr[-6000:])
    return r.stdout


def probe(path):
    return json.loads(run(['ffprobe', '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)]))


def stamp(t, ass=False):
    h = int(t // 3600); m = int(t % 3600 // 60); s = t % 60
    return f'{h}:{m:02d}:{s:05.2f}' if ass else f'{h:02d}:{m:02d}:{int(s):02d},{round((s % 1)*1000):03d}'


def subtitles(plan):
    header = '''[Script Info]
ScriptType: v4.00+
PlayResX: 1920
PlayResY: 1080
ScaledBorderAndShadow: yes
WrapStyle: 2

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Main,Microsoft YaHei,56,&H00F7F9FF,&H000000FF,&H600E1017,&H90101010,0,0,0,0,100,100,3,0,1,1.6,1.2,2,100,100,100,1
Style: Product,Microsoft YaHei,56,&H00283140,&H000000FF,&HFF000000,&HFF000000,0,0,0,0,100,100,3,0,1,0,0,2,100,100,100,1
Style: Brand,Microsoft YaHei,118,&H00F2F5FF,&H000000FF,&H800E1017,&H90101010,-1,0,0,0,100,100,7,0,1,0,1,7,110,110,340,1
Style: Small,Microsoft YaHei,28,&H00F2F5FF,&H000000FF,&H50000000,&H90101010,0,0,0,0,100,100,0,0,1,1,0.8,2,80,80,54,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
'''
    events = []
    srt = []
    for i, c in enumerate(plan['captions'], 1):
        start, end, text = c['start'], c['end'], c['text']
        srt_text = text
        if c.get('style') == 'brand':
            srt_text += '\n' + c['supporting_text'] + '\n' + c['disclosure']
        srt.append(f'{i}\n{stamp(start)} --> {stamp(end)}\n{srt_text}\n')
        if c.get('style') == 'brand':
            events.append(f'Dialogue: 0,{stamp(start,True)},{stamp(end,True)},Brand,,0,0,0,,{{\\fad(650,600)}}烟火地图')
            events.append(f'Dialogue: 0,{stamp(start+0.7,True)},{stamp(end,True)},Main,,0,0,0,,{{\\an7\\pos(118,508)\\fs43\\fsp3\\fad(650,600)}}找到惦记的那一口。')
            events.append(f'Dialogue: 0,{stamp(start+1.1,True)},{stamp(end,True)},Small,,0,0,0,,{{\\an7\\pos(120,597)\\fs26\\fad(650,600)}}{c["supporting_text"]}')
            events.append(f'Dialogue: 0,{stamp(start,True)},{stamp(end,True)},Small,,0,0,0,,{{\\fad(650,600)}}{c["disclosure"]}')
        else:
            style = 'Product' if c.get('style') == 'product' else 'Main'
            fade_in, fade_out = c.get('fade_in',450), c.get('fade_out',450)
            events.append(f'Dialogue: 0,{stamp(start,True)},{stamp(end,True)},{style},,0,0,0,,{{\\fad({fade_in},{fade_out})}}{text}')
    (ROOT/'captions.ass').write_text(header+'\n'.join(events)+'\n',encoding='utf-8-sig')
    (ROOT/'captions.srt').write_text('\n'.join(srt),encoding='utf-8-sig')


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--download', action='store_true')
    parser.add_argument('--preview', action='store_true', help='720p review render; final is 1080p')
    args = parser.parse_args()
    plan = json.loads((ROOT/'timeline.json').read_text(encoding='utf-8'))
    assert not plan['narration'] and abs(sum(s['duration'] for s in plan['shots'])-60)<0.001
    assert plan['fps'] == FPS and round(plan['duration'] * FPS) == 1800
    WORK.mkdir(exist_ok=True); STOCK.mkdir(parents=True,exist_ok=True)
    if args.download:
        for asset in json.loads((ROOT/'sources.json').read_text(encoding='utf-8'))['videos']:
            path = STOCK/asset['file']
            if not path.exists():
                print('Downloading',asset['file'],flush=True)
                with urllib.request.urlopen(asset['download'],timeout=60) as response, path.open('wb') as out:
                    while chunk := response.read(1024*1024): out.write(chunk)
    subtitles(plan)
    width,height = (1280,720) if args.preview else (1920,1080)
    parts=[]
    for i,shot in enumerate(plan['shots']):
        still = shot.get('kind') == 'still'
        path = ROOT/shot['source'] if still else STOCK/shot['source']
        if not path.exists(): raise FileNotFoundError(path)
        speed = shot.get('speed',1)
        if not still:
            meta=probe(path)
            if float(meta['format']['duration'])+0.03 < shot.get('in',0)+shot['duration']*speed:
                raise ValueError(f'Source too short for shot {i+1}')
        out=WORK/f'{width}-shot-{i+1:02d}.mp4'
        key=hashlib.sha256(json.dumps([shot,width,height,FPS,GRADES,COLOR_PIPELINE_VERSION],sort_keys=True).encode()+str(path.stat().st_mtime_ns).encode()).hexdigest()
        stampfile=out.with_suffix('.sha256')
        if out.exists() and stampfile.exists() and stampfile.read_text()==key:
            parts.append(out);continue
        inp=['-loop','1','-framerate',str(FPS),'-i',str(path)] if still else ['-ss',str(shot.get('in',0)),'-i',str(path)]
        filters=[f'setpts=(PTS-STARTPTS)/{speed}']
        if shot.get('crop'):filters.append('crop='+shot['crop'])
        if shot.get('motion')=='gentle-push':
            # Subtle post-production motion only; this is explicitly an AI still.
            filters += [f'scale={width*2}:{height*2}:force_original_aspect_ratio=increase',f'crop={width*2}:{height*2}',f"zoompan=z='1+on*0.003/{FPS}':x='iw/2-iw/zoom/2':y='ih/2-ih/zoom/2':d=1:s={width}x{height}:fps={FPS}"]
        else:filters += [f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos',f'crop={width}:{height}',f'fps={FPS}']
        if GRADES[shot['grade']]:filters.append(GRADES[shot['grade']])
        if i==0:filters.append('fade=t=in:st=0:d=0.4')
        if i==len(plan['shots'])-1:filters.append('fade=t=out:st=6.2:d=0.8')
        # Stills and stock footage may differ in range/matrix metadata. Normalize
        # both pixel values and tags so concat never reinitializes the subtitle graph.
        filters += ['scale=in_range=auto:out_range=tv:out_color_matrix=bt709',
                    'setsar=1','format=yuv420p',
                    'setparams=range=limited:color_primaries=bt709:color_trc=bt709:colorspace=bt709']
        print(f'Rendering {i+1}/{len(plan["shots"])}: {shot["note"]}',flush=True)
        run(['ffmpeg','-y','-hide_banner','-loglevel','error',*inp,'-an','-vf',','.join(filters),'-frames:v',str(round(shot['duration']*FPS)),'-c:v','libx264','-preset','fast','-crf','18','-threads','4','-pix_fmt','yuv420p','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-video_track_timescale',str(FPS*1000),str(out)])
        count=int(next(s for s in probe(out)['streams'] if s['codec_type']=='video')['nb_frames'])
        assert count==round(shot['duration']*FPS),(i,count)
        stampfile.write_text(key)
        parts.append(out)
    # Paths are generated locally, never shell-interpolated. FFmpeg concat uses quoted POSIX paths.
    concat=WORK/f'concat-{width}.txt'
    concat.write_text('\n'.join("file '"+p.as_posix().replace("'","'\\''")+"'" for p in parts),encoding='utf-8')
    base=WORK/f'picture-{width}.mp4'
    run(['ffmpeg','-y','-v','error','-f','concat','-safe','0','-i',str(concat),'-an','-c','copy',str(base)])
    output=ROOT/('yanhuo-ad-review-720p.mp4' if args.preview else 'yanhuo-ad-60s-1080p.mp4')
    print('Finishing subtitles and original instrumental music (no narration).',flush=True)
    run(['ffmpeg','-y','-hide_banner','-loglevel','error','-i',str(base),'-i',str(ROOT/'audio/original-bed.wav'),'-map','0:v:0','-map','1:a:0','-vf','subtitles=captions.ass','-af','loudnorm=I=-18:TP=-1.5:LRA=8','-t','60','-c:v','libx264','-preset','medium','-crf','18','-threads','4','-pix_fmt','yuv420p','-color_range','tv','-colorspace','bt709','-color_primaries','bt709','-color_trc','bt709','-c:a','aac','-b:a','192k','-ar','48000','-movflags','+faststart','-metadata','title=烟火地图 · 找到惦记的那一口','-metadata','comment=No voiceover. Licensed stock montage and AI-generated end visual. Credits: sources.json.',str(output)])
    details=probe(output)
    video = next(s for s in details['streams'] if s['codec_type']=='video')
    assert video['r_frame_rate'] == '30/1' and int(video['nb_frames']) == 1800
    (ROOT/('review-probe.json' if args.preview else 'video-probe.json')).write_text(json.dumps(details,ensure_ascii=False,indent=2),encoding='utf-8')
    run(['ffmpeg','-y','-v','error','-ss','56','-i',str(output),'-frames:v','1',str(ROOT/'poster.jpg')])
    run(['ffmpeg','-y','-v','error','-i',str(output),'-vf','fps=1/4,scale=480:-2,tile=5x3','-frames:v','1',str(ROOT/'contact-sheet.jpg')])
    print('Complete:',output,output.stat().st_size,flush=True)


if __name__=='__main__':main()
