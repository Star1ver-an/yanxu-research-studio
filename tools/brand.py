"""Single geometry source for the Yanxu vector mark and Windows icon."""
from pathlib import Path
import argparse
import math
from PIL import Image,ImageColor,ImageDraw,ImageFilter,ImageFont

LIGHT={'base':'#e4e8ec','ink':'#303c48','accent':'#a34f30','muted':'#586574','shade':'#bec5cd','shine':'#ffffff'}
DARK={'base':'#2c323a','ink':'#e8edf2','accent':'#efb08b','muted':'#b2bdc9','shade':'#1d2228','shine':'#3c4550'}
# Two folded leaves form a Y. A narrow cut keeps the right leaf independent.
LEAVES=[[(20,22),(29.5,22),(44,42),(44,60),(36,60),(36,45)],
        [(50,22),(60,22),(46.5,40.5),(41.5,33.5)]]


def corners(points,radius=.65):
    rounded=[]
    for index,point in enumerate(points):
        before,after=points[index-1],points[(index+1)%len(points)]
        def towards(other):
            length=math.dist(point,other)
            offset=min(radius,length/3)
            return tuple(point[i]+(other[i]-point[i])*offset/length for i in (0,1))
        rounded.append((towards(before),point,towards(after)))
    return rounded


def path_data(points):
    values=corners(points)
    def xy(p):return f'{p[0]:.3f} {p[1]:.3f}'
    return 'M'+xy(values[-1][2])+''.join('L'+xy(a)+'Q'+xy(b)+' '+xy(c) for a,b,c in values)+'Z'


def svg(palette=LIGHT,tile=False):
    base=f'<rect x="2" y="2" width="76" height="76" rx="23" fill="{palette["base"]}"/>' if tile else ''
    paths=''.join(f'<path d="{path_data(p)}"/>' for p in LEAVES)
    return f'<svg xmlns="http://www.w3.org/2000/svg" width="80" height="80" viewBox="0 0 80 80"><title>研序 · 双页 Y 标识</title>{base}<g fill="{palette["accent"]}">{paths}</g></svg>\n'


def render(size,palette=LIGHT,tile=True):
    factor=4;scale=size*factor/80
    image=Image.new('RGBA',(size*factor,size*factor))
    draw=ImageDraw.Draw(image)
    if tile:
        draw.rounded_rectangle((2*scale,2*scale,78*scale,78*scale),radius=23*scale,fill=palette['base'])
    for leaf in LEAVES:
        outline=[]
        for a,b,c in corners(leaf):
            outline.append(a)
            for i in range(1,17):
                t=i/16
                outline.append(tuple((1-t)**2*a[k]+2*(1-t)*t*b[k]+t*t*c[k] for k in (0,1)))
        draw.polygon([(x*scale,y*scale) for x,y in outline],fill=palette['accent'])
    return image.resize((size,size),Image.Resampling.LANCZOS)


def write_assets(root):
    root=Path(root);brand=root/'resources'/'brand';brand.mkdir(parents=True,exist_ok=True)
    (root/'web'/'mark.svg').write_text(svg(),encoding='utf-8')
    (root/'web'/'favicon.svg').write_text(svg(tile=True),encoding='utf-8')
    (brand/'yanxu-mark.svg').write_text(svg(),encoding='utf-8')
    (brand/'yanxu-icon-light.svg').write_text(svg(LIGHT,True),encoding='utf-8')
    (brand/'yanxu-icon-dark.svg').write_text(svg(DARK,True),encoding='utf-8')
    render(1024,tile=False).save(brand/'yanxu-mark.png')
    render(512).save(brand/'yanxu-app-icon.png')
    icon=root/'resources'/'yanxu.ico'
    render(256).save(icon,sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])
    return icon


def preview(root):
    """A shareable brand board, rendered from the same production geometry."""
    board=Image.new('RGB',(1600,900),LIGHT['base'])
    draw=ImageDraw.Draw(board);draw.rectangle((800,0,1600,900),fill=DARK['base'])
    fontroot=Path('C:/Windows/Fonts')
    chinese=ImageFont.truetype(str(fontroot/'msyh.ttc'),58)
    latin=ImageFont.truetype(str(fontroot/'segoeui.ttf'),15)
    small=ImageFont.truetype(str(fontroot/'segoeui.ttf'),13)
    def tracked(text,x,y,font,fill,spacing):
        width=sum(draw.textlength(c,font=font) for c in text)+spacing*(len(text)-1)
        cursor=x-width/2
        for c in text:
            draw.text((cursor,y),c,font=font,fill=fill);cursor+=draw.textlength(c,font=font)+spacing
    for offset,palette in [(0,LIGHT),(800,DARK)]:
        center=offset+400
        tracked('YANXU / RESEARCH STUDIO',center,58,small,palette['muted'],2)
        # A shallow raised tile follows the application's upper-left light source.
        for dx,dy,color in [(8,8,palette['shade']),(-7,-7,palette['shine'])]:
            shadow=Image.new('RGBA',board.size,(*ImageColor.getrgb(color),0))
            ImageDraw.Draw(shadow).rounded_rectangle((center-75+dx,227+dy,center+75+dx,377+dy),radius=45,fill=color)
            shadow=shadow.filter(ImageFilter.GaussianBlur(12));board.paste(shadow,(0,0),shadow)
        tile=render(164,palette);board.paste(tile,(center-82,220),tile)
        draw=ImageDraw.Draw(board)
        tracked('研序',center,423,chinese,palette['ink'],8)
        tracked('RESEARCH STUDIO',center,516,latin,palette['muted'],4)
        draw.line((center-60,601,center+60,601),fill=palette['muted'],width=1)
        for size,x in [(16,center-125),(24,center-58),(32,center+14),(48,center+91)]:
            icon=render(size,palette);board.paste(icon,(x-size//2,667-size//2),icon)
        tracked('QUIET FORM. CLEAR DIRECTION.',center,790,small,palette['muted'],2)
    destination=Path(root)/'dist'/'研序-Logo预览.png';destination.parent.mkdir(exist_ok=True)
    board.save(destination)
    return destination


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--preview',action='store_true')
    args=parser.parse_args();root=Path(__file__).resolve().parent.parent
    print(write_assets(root))
    if args.preview:print(preview(root))
