"""Reproducible vector brand and size-tuned native Windows icon."""
from pathlib import Path
from PIL import Image, ImageDraw

ROOT=Path(__file__).resolve().parents[1]
SVG='''<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64"><defs><linearGradient id="bg" x2="1" y2="1"><stop stop-color="#187e73"/><stop offset="1" stop-color="#084c48"/></linearGradient></defs><rect x="1" y="1" width="62" height="62" rx="16" fill="url(#bg)"/><rect x="12" y="13" width="37" height="30" rx="7" fill="none" stroke="#7cd7ba" stroke-width="3"/><rect x="18" y="21" width="35" height="30" rx="7" fill="#f2fff5"/><path d="M32 28 Q30 27 30 30V42 Q30 45 33 43L43 37 Q45 35.5 42 34Z" fill="#106b60"/></svg>'''

def raster(size):
    scale=4;side=size*scale
    im=Image.new('RGBA',(side,side));mask=Image.new('L',(side,side));d=ImageDraw.Draw(mask)
    def box(coords):return tuple(round(v/64*side) for v in coords)
    d.rounded_rectangle(box((1,1,63,63)),radius=16/64*side,fill=255)
    bg=Image.new('RGBA',(side,side));pixels=bg.load()
    for y in range(side):
        for x in range(side):
            t=(x+y)/(2*side);pixels[x,y]=(round(24-16*t),round(126-50*t),round(115-43*t),255)
    im.paste(bg,(0,0),mask);d=ImageDraw.Draw(im)
    d.rounded_rectangle(box((12,13,49,43)),radius=7/64*side,outline='#7cd7ba',width=max(1,round(3/64*side)))
    d.rounded_rectangle(box((18,21,53,51)),radius=7/64*side,fill='#f2fff5')
    d.polygon([box((31,28)),box((31,44)),box((44,36))],fill='#106b60')
    return im.resize((size,size),Image.Resampling.LANCZOS)

if __name__=='__main__':
    (ROOT/'static/icon.svg').write_text(SVG,encoding='utf-8')
    raster(512).save(ROOT/'static/icon.png')
    sizes=[16,20,24,32,48,64,128,256]
    frames=[raster(n) for n in sizes]
    frames[-1].save(ROOT/'static/icon.ico',format='ICO',sizes=[(n,n) for n in sizes],append_images=frames[:-1])
