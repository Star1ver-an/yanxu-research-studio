"""Build and render a reproducible Word figure/pagination acceptance fixture.

Run with the dependency loader's Python. This helper is deliberately inert on
import. It never downloads/installs a renderer or uses a user's Word instance.
Pass a dedicated LibreOffice executable and the documents skill renderer for
visual acceptance; PNG review remains a separate, explicit human/agent step.
"""
import argparse
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import uuid

ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0,str(ROOT))


def fixture_png(path, tall=False):
    from PIL import Image,ImageDraw,ImageFont
    width,height=(900,1500) if tall else (1800,1000)
    image=Image.new('RGB',(width,height),'white')
    draw=ImageDraw.Draw(image)
    candidates=[Path(os.environ.get('WINDIR','C:/Windows'))/'Fonts/msyh.ttc',
                Path('/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf')]
    font_path=next((p for p in candidates if p.exists()),None)
    font=ImageFont.truetype(str(font_path),36 if tall else 40) if font_path else ImageFont.load_default(size=32)
    small=ImageFont.truetype(str(font_path),28 if tall else 32) if font_path else font
    draw.text((45,35),'模型压缩与推理性能评估',font=font,fill='#172b36')
    if tall:
        steps=['导入实验数据','核对样本与评估指标','保留测试集评估结果','比较精度和推理时延','记录可复现配置']
        for i,label in enumerate(steps):
            y=160+i*245
            draw.rounded_rectangle((95,y,width-95,y+140),radius=18,fill='#eef4f5',outline='#43666c',width=3)
            draw.text((width//2,y+65),label,font=small,fill='#172b36',anchor='mm')
            if i<len(steps)-1:
                draw.line((width//2,y+145,width//2,y+235),fill='#43666c',width=5)
                draw.polygon([(width//2-13,y+220),(width//2+13,y+220),(width//2,y+240)],fill='#43666c')
    else:
        x0,y0,x1,y1=200,150,width-80,height-210
        draw.line((x0,y0,x0,y1,x1,y1),fill='#172b36',width=3)
        series=[(0.923,76,'未压缩基线模型'),(.910,42,'结构化剪枝与蒸馏模型'),(.896,19,'整数量化后边缘部署模型')]
        colors=['#396b80','#4c8775','#a26f4d']
        for i,((accuracy,latency,label),color) in enumerate(zip(series,colors)):
            x=x0+int(latency/90*(x1-x0));y=y1-int((accuracy-.87)/.07*(y1-y0))
            draw.ellipse((x-13,y-13,x+13,y+13),fill=color)
            draw.text((x-320 if i==0 else x+20,y-55 if i==0 else y+20),label,font=small,fill=color)
            draw.text((x0+i*480,y1+80),f'{label}\n准确率 {accuracy:.3f}  时延 {latency} ms',font=small,fill='#172b36')
        draw.text((x0+350,height-70),'单次推理时延  毫秒',font=small,fill='#172b36')
        draw.text((50,95),'准确率',font=small,fill='#172b36')
    image.save(path,format='PNG',dpi=(180,180))
    return path.read_bytes()


def create_fixture(output, plot_png=None):
    from app.word_export import make_docx
    output.mkdir(parents=True,exist_ok=True)
    wide=Path(plot_png).read_bytes() if plot_png else fixture_png(output/'performance.png')
    tall=fixture_png(output/'workflow.png',tall=True)
    rows=['| 记录 | 模型与测试条件 | 准确率 | 时延毫秒 |',
          '|---|---|---|---|']
    for i in range(1,79):
        rows.append(f'| {i:02} | 边缘图像分类评估  第 {i} 次独立配置核验  保留原始测试集 | {0.82+(i%10)/100:.2f} | {12+i%31} |')
    md='\n'.join([
        '# 人工智能模型实验评估',
        '本研究比较模型性能与部署条件，并保留数据来源、评估条件和绘图配置以便复现。',
        '## 性能比较',
        '图中展示模型性能评估结果，坐标轴与图例保留已经核对的原有定义。',
        '[figure:performance]',
        '## 评估记录',
        '下表列出配置核验记录。跨页时应保持表头、列宽和文字换行一致。',
        *rows,
        '## 结果解释',
        '压缩策略应综合考虑精度下降幅度、部署限制与推理时延。所有图表均应对应保存的数据版本。',
        '流程图采用纵向布局。图像保持宽高比例，图注与图片应位于同一页。',
    ])
    figures=[dict(id='performance',title='模型性能评估结果',png=wide,
                  caption='横纵轴及分组沿用已核对的绘图配置。图像来自本地确定性绘图引擎，误差范围未提供时不生成误差线。'),
             dict(id='workflow',title='模型压缩评估与复现流程',png=tall,
                  caption='流程包含数据导入、指标核对、测试集评估、精度与时延比较以及可复现配置保存。')]
    docx=output/'figure-pagination.docx'
    docx.write_bytes(make_docx(md,figures=figures))
    (output/'fixture.md').write_text(md,encoding='utf-8')
    return docx


def check_pdf(pdf, output):
    from pypdf import PdfReader
    from pypdf.generic import ContentStream
    reader=PdfReader(pdf)
    def drawn_images(contents, resources, seen=None):
        # page.images enumerates all shared resources, even unused images. Count
        # painting operators instead so orphaned captions cannot falsely pass.
        seen=set() if seen is None else seen
        count=0
        if contents is None:
            return 0
        resources=resources.get_object()
        for operands,operator in ContentStream(contents,reader).operations:
            if operator==b'INLINE IMAGE':
                count+=1
            elif operator==b'Do':
                xobjects=resources.get('/XObject',{})
                if hasattr(xobjects,'get_object'):
                    xobjects=xobjects.get_object()
                obj=xobjects.get(operands[0])
                if obj is None:
                    continue
                obj=obj.get_object()
                if obj.get('/Subtype')=='/Image':
                    count+=1
                elif obj.get('/Subtype')=='/Form' and id(obj) not in seen:
                    seen.add(id(obj))
                    count+=drawn_images(obj,obj.get('/Resources',resources),seen)
        return count
    pages=[]
    for number,page in enumerate(reader.pages,1):
        text=page.extract_text() or ''
        pages.append(dict(number=number,text_characters=len(text),image_count=drawn_images(page.get_contents(),page['/Resources']),
                          has_table_header='准确率' in text and '模型与测试条件' in text,
                          has_figure_1_caption='图 1' in text,
                          has_figure_2_caption='图 2' in text))
        (output/f'page-{number}.txt').write_text(text,encoding='utf-8')
    captions_paired=all(not (p['has_figure_1_caption'] or p['has_figure_2_caption']) or p['image_count']>0 for p in pages)
    return dict(page_count=len(reader.pages),pages=pages,captions_share_image_page=captions_paired)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',default=str(ROOT/'qa-runs/plotting-m01/word'))
    parser.add_argument('--input',help='Render an existing DOCX instead of generating the fixture.')
    parser.add_argument('--plot-png',help='Use an existing engine PNG in the generated fixture.')
    parser.add_argument('--renderer',help='Absolute path to documents skill render_docx.py.')
    parser.add_argument('--soffice',help='Absolute path to dedicated QA LibreOffice soffice.exe.')
    parser.add_argument('--poppler-dir',help='Absolute bundled Poppler executable directory.')
    args=parser.parse_args()
    output=Path(args.output).resolve()
    output.mkdir(parents=True,exist_ok=True)
    docx=Path(args.input).resolve() if args.input else create_fixture(output,args.plot_png)
    report=dict(document=str(docx),visual_review='pending',rendered=False)
    if args.renderer:
        renderer=Path(args.renderer).resolve()
        env=os.environ.copy()
        extra=[]
        if args.soffice:
            soffice=Path(args.soffice).resolve()
            if not soffice.is_file():
                raise FileNotFoundError(soffice)
            extra.append(str(soffice.parent))
        if args.poppler_dir:
            extra.append(str(Path(args.poppler_dir).resolve()))
        env['PATH']=os.pathsep.join(extra+[env.get('PATH','')])
        env['PYTHONUTF8']='1'
        # A fresh run directory preserves prior evidence and excludes stale PNGs
        # if a layout fix reduces page count.
        rendered=output/('rendered-'+uuid.uuid4().hex[:10])
        command=[sys.executable,str(renderer),str(docx),'--output_dir',str(rendered),'--dpi','150','--emit_pdf','--verbose']
        process=subprocess.run(command,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',
                               creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0),timeout=240)
        (output/'render.log').write_text(process.stdout+'\n'+process.stderr,encoding='utf-8')
        if process.returncode:
            raise RuntimeError('DOCX rendering failed; inspect '+str(output/'render.log'))
        images=sorted(rendered.glob('page-*.png'),key=lambda p:int(p.stem.split('-')[-1]))
        pdf=rendered/(docx.stem+'.pdf')
        if not images or not pdf.is_file():
            raise RuntimeError('Renderer did not create both page PNGs and PDF.')
        report.update(rendered=True,page_images=[str(p) for p in images],pdf=str(pdf),**check_pdf(pdf,rendered))
        if report['page_count']!=len(images) or not report['captions_share_image_page']:
            (output/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
            raise RuntimeError('Pagination checks failed; inspect rendered pages.')
    (output/'acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':
    main()
