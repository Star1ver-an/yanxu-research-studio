"""Readable A4 export for the Markdown subset used by stage reports."""
import io
import re
import warnings

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


MAX_FIGURES = 80
MAX_PNG_BYTES = 20 * 1024 * 1024
MAX_PNG_PIXELS = 40_000_000
MAX_DOCUMENT_PNG_BYTES = 100 * 1024 * 1024
FIGURE_MARKER = re.compile(r'\[figure:([A-Za-z0-9_-]{1,80})\]')


def _validated_figures(figures):
    """Accept application-owned bytes only; Markdown paths are never opened."""
    from PIL import Image

    if figures is None:
        return {}
    if not isinstance(figures, (list, tuple)) or len(figures) > MAX_FIGURES:
        raise ValueError('Word 图表列表格式无效或数量超过 80 张。')
    prepared = {}
    total_bytes = 0
    for number, figure in enumerate(figures, 1):
        if not isinstance(figure, dict):
            raise ValueError('Word 图表记录格式无效。')
        identity = figure.get('id')
        if not isinstance(identity, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', identity):
            raise ValueError('Word 图表标识无效。')
        if identity in prepared:
            raise ValueError('Word 图表标识重复，请重新选择图表。')
        raw = figure.get('png')
        if not isinstance(raw, bytes) or not raw or len(raw) > MAX_PNG_BYTES:
            raise ValueError('Word 图表必须提供不超过 20 MB 的 PNG 字节。')
        total_bytes += len(raw)
        if total_bytes > MAX_DOCUMENT_PNG_BYTES:
            raise ValueError('Word 图表图片合计超过 100 MB，请分批导出。')
        if not raw.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('Word 图表仅支持经过验证的 PNG 图片。')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                with Image.open(io.BytesIO(raw)) as image:
                    width, height = image.size
                    if image.format != 'PNG' or width < 1 or height < 1 or width * height > MAX_PNG_PIXELS:
                        raise ValueError('Word 图表尺寸无效或超过 4000 万像素。')
                    image.verify()
        except Exception as exc:
            raise ValueError('Word 图表 PNG 损坏或尺寸超出限制。') from exc
        title = str(figure.get('title') or '研究图表').strip()
        caption = str(figure.get('caption') or '').strip()
        provenance = figure.get('provenance') or ''
        if not isinstance(provenance,str) or len(provenance)>6000:
            raise ValueError('Word 图表溯源信息无效或过长。')
        if any(ord(char)<32 and char not in '\n\t' for char in title+caption+provenance):
            raise ValueError('Word 图表标题或图注含无效控制字符。')
        if len(title) > 300 or len(caption) > 2000 or len(title)+len(caption)>1800:
            raise ValueError('Word 图表标题或图注过长，请缩短后导出。')
        prepared[identity] = dict(id=identity, number=number, png=raw, width=width,
                                  height=height, title=title, caption=caption,provenance=provenance)
    return prepared


def inline(paragraph, text, bold=None, italic=None):
    tokens = re.split(r'(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*|\[[^\]]+\]\(https?://[^\s)]+\))', text)
    for token in tokens:
        if not token:
            continue
        if token.startswith('**') and token.endswith('**'):
            inline(paragraph, token[2:-2], True, italic)
        elif token.startswith('*') and token.endswith('*'):
            inline(paragraph, token[1:-1], bold, True)
        else:
            link=re.fullmatch(r'\[([^\]]+)\]\((https?://[^\s)]+)\)',token)
            if link:
                token=link[1]+' ('+link[2]+')'
            code=token.startswith('`') and token.endswith('`')
            run=paragraph.add_run(token[1:-1] if code else token)
            run.bold=bold
            run.italic=italic
            if code:
                run.font.name='Consolas'
                run.font.size=Pt(10)


def make_docx(md, figures=None, locale='zh-CN'):
    """Render reports and trusted figures without resolving arbitrary image URLs.

    ``[figure:<id>]`` places a supplied figure in the text. Repeated markers are
    cross references; missing ones stay visible as a repair message. Remaining
    figures are appended in their supplied order, retaining stable numbering.
    """
    prepared_figures = _validated_figures(figures)
    figure_label={'zh-CN':'图 ','zh-TW':'圖 ','en':'Figure ','ja':'図 ','ko':'그림 '}.get(locale,'Figure ')
    east_font={'zh-CN':'宋体','zh-TW':'Microsoft JhengHei','en':'Calibri','ja':'Yu Gothic','ko':'Malgun Gothic'}.get(locale,'Calibri')
    document=Document()
    document.core_properties.author=''
    section=document.sections[0]
    section.page_width,section.page_height=Cm(21),Cm(29.7)
    section.top_margin=section.bottom_margin=Cm(2.2)
    section.left_margin=section.right_margin=Cm(2.2)
    for name in ['Normal','Title','Heading 1','Heading 2','Heading 3','Heading 4','List Bullet','List Number','Caption']:
        style=document.styles[name]
        style.font.name='Calibri'
        style.font.color.rgb=RGBColor(0,0,0)
        style.font.underline=False
        style.font.italic=False
        style.font.bold=False
        style.font.size=Pt(11)
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),east_font)
        lang=OxmlElement('w:lang');lang.set(qn('w:val'),locale);lang.set(qn('w:eastAsia'),locale);style._element.rPr.append(lang)
        for attr in ['asciiTheme','hAnsiTheme','eastAsiaTheme','cstheme']:
            style._element.rPr.rFonts.attrib.pop(qn('w:'+attr),None)
        for border in list(style._element.get_or_add_pPr().findall(qn('w:pBdr'))):
            style._element.pPr.remove(border)
        fmt=style.paragraph_format
        fmt.line_spacing=1.2
        fmt.space_after=Pt(6)
        fmt.widow_control=True
        fmt.keep_with_next=False
    for name,size in [('Title',20),('Heading 1',16),('Heading 2',14),('Heading 3',12),('Heading 4',11)]:
        style=document.styles[name]
        style.font.size=Pt(size)
        style.font.bold=True
        style._element.rPr.rFonts.set(qn('w:eastAsia'),'黑体' if locale=='zh-CN' else east_font)
        style.paragraph_format.space_before=Pt(12)
        style.paragraph_format.space_after=Pt(7)
        style.paragraph_format.keep_with_next=True
        style.paragraph_format.keep_together=True
    title_used=False
    code=False
    table_rows=[]
    placed_figures=set()

    def insert_figure(identity):
        figure=prepared_figures.get(identity)
        if figure is None:
            p=document.add_paragraph()
            missing={'zh-CN':'〔图表缺失：{id}，请重新关联后导出〕','zh-TW':'〔圖表缺失：{id}，請重新關聯後匯出〕','en':'[Missing figure: {id}. Relink it before exporting.]','ja':'［図表が見つかりません：{id}。関連付けを確認してからエクスポートしてください。］','ko':'[그림 누락: {id}. 다시 연결한 후 내보내세요.]'}
            inline(p,missing.get(locale,missing['en']).format(id=identity))
            return
        if identity in placed_figures:
            references={'zh-CN':'（见图 {n}）','zh-TW':'（見圖 {n}）','en':'(See Figure {n})','ja':'（図 {n} 参照）','ko':'(그림 {n} 참조)'}
            inline(document.add_paragraph(),references.get(locale,references['en']).format(n=figure['number']))
            return
        placed_figures.add(identity)
        caption_text=figure['caption']
        if caption_text.startswith(figure['title']) and (
                len(caption_text)==len(figure['title']) or caption_text[len(figure['title'])] in '。.:：\n '):
            caption=figure_label+str(figure['number'])+'  '+caption_text
        else:
            caption=figure_label+str(figure['number'])+'  '+figure['title']
            if caption_text:
                caption+='。'+caption_text
        # Fit the complete figure and a reasonable caption on an A4 page. Only
        # downscale by one ratio, independent of unreliable image DPI metadata.
        usable_width=int(section.page_width-section.left_margin-section.right_margin)
        usable_height=int(section.page_height-section.top_margin-section.bottom_margin)
        estimated_caption_lines=max(1,(len(caption)+40)//41)
        caption_reserve=Cm(min(21,1.4+estimated_caption_lines*.43))
        max_height=min(Cm(18.5),max(Cm(4),usable_height-int(caption_reserve)))
        ratio=min(usable_width/figure['width'],max_height/figure['height'])
        width=int(figure['width']*ratio)
        height=int(figure['height']*ratio)
        p=document.add_paragraph()
        p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_with_next=True
        p.paragraph_format.keep_together=True
        # Multiple line spacing also enlarges inline picture line boxes in
        # LibreOffice, pushing a caption to the following page. Use single.
        p.paragraph_format.line_spacing=1.0
        p.paragraph_format.space_before=Pt(8)
        p.paragraph_format.space_after=Pt(4)
        shape=p.add_run().add_picture(io.BytesIO(figure['png']),width=width,height=height)
        description=caption
        if figure['provenance']:
            description+='\n溯源：'+figure['provenance']
        shape._inline.docPr.set('descr',description)
        p=document.add_paragraph(style='Caption')
        p.alignment=WD_ALIGN_PARAGRAPH.CENTER
        p.paragraph_format.keep_together=True
        p.paragraph_format.keep_with_next=False
        p.paragraph_format.line_spacing=1.15
        p.paragraph_format.space_after=Pt(10)
        inline(p,caption)
        for run in p.runs:
            run.font.size=Pt(10)

    def flush_table():
        nonlocal table_rows
        if not table_rows:
            return
        rows=[row for row in table_rows if not all(re.fullmatch(r'[:\- ]+',cell or '-') for cell in row)]
        table_rows=[]
        if not rows:
            return
        cols=max(map(len,rows))
        table=document.add_table(rows=0,cols=cols)
        table.style='Table Grid'
        table.autofit=False
        borders=OxmlElement('w:tblBorders')
        for edge in ['top','left','bottom','right','insideH','insideV']:
            border=OxmlElement('w:'+edge)
            for key,value in [('val','single'),('sz','4'),('color','D9D9D9')]:
                border.set(qn('w:'+key),value)
            borders.append(border)
        table._tbl.tblPr.append(borders)
        # Allocate more room to descriptive columns, without allowing a very short
        # label or one long URL to squeeze the rest of the table.
        weights=[min(38,max(8,max((len(row[i]) if i<len(row) else 0) for row in rows))) for i in range(cols)]
        widths=[Cm(16.6*w/sum(weights)) for w in weights]
        for col,width in zip(table.columns,widths):
            col.width=width
        for index,row in enumerate(rows):
            cells=table.add_row().cells
            trpr=table.rows[-1]._tr.get_or_add_trPr()
            # Rows taller than one page must be allowed to split to prevent loss.
            if max(map(len,row),default=0)<500:
                trpr.append(OxmlElement('w:cantSplit'))
            if index==0:
                trpr.append(OxmlElement('w:tblHeader'))
            for i,cell in enumerate(cells):
                cell.width=widths[i]
                cell.vertical_alignment=1
                props=cell._tc.get_or_add_tcPr()
                margins=OxmlElement('w:tcMar')
                for edge,value in [('top',80),('bottom',80),('left',100),('right',100)]:
                    node=OxmlElement('w:'+edge)
                    node.set(qn('w:w'),str(value))
                    node.set(qn('w:type'),'dxa')
                    margins.append(node)
                props.append(margins)
                if index==0:
                    shade=OxmlElement('w:shd');shade.set(qn('w:fill'),'F1F3F1');props.append(shade)
                p=cell.paragraphs[0]
                p.paragraph_format.space_after=Pt(3)
                p.paragraph_format.keep_with_next=index==0
                p.paragraph_format.line_spacing=1.1
                inline(p,row[i] if i<len(row) else '',bold=index==0)
                for run in p.runs:
                    run.font.size=Pt(10)
        document.add_paragraph().paragraph_format.space_after=Pt(2)

    def content_line(line):
        """Handle one non-table line, including markers embedded in prose."""
        nonlocal title_used
        markers=list(FIGURE_MARKER.finditer(line)) if not code else []
        if markers:
            start=0
            for match in markers:
                prefix=line[start:match.start()].strip()
                if prefix:
                    content_line(prefix)
                insert_figure(match[1])
                start=match.end()
            suffix=line[start:].strip()
            if suffix:
                content_line(suffix)
            return
        if not line.strip() or (not code and re.fullmatch(r'[-*_]{3,}',line.strip())):
            return
        heading=re.match(r'^(#{1,6})\s+(.*)',line) if not code else None
        if heading:
            style='Title' if not title_used else 'Heading '+str(min(4,len(heading[1])))
            title_used=True
            inline(document.add_paragraph(style=style),heading[2])
        elif code:
            p=document.add_paragraph();run=p.add_run(line);run.font.name='Consolas';run.font.size=Pt(9)
        else:
            bullet=re.match(r'^\s*[-*]\s+(.*)',line)
            numbered=re.match(r'^\s*\d+[.)]\s+(.*)',line)
            p=document.add_paragraph(style='List Bullet' if bullet else None)
            if 'https://doi.org/' in line and len(line)<800:
                p.paragraph_format.keep_together=True
            if numbered:
                p.paragraph_format.left_indent=Cm(.5)
                p.paragraph_format.first_line_indent=Cm(-.5)
            if line.startswith('>'):
                p.paragraph_format.left_indent=Cm(.5)
                line=re.sub(r'^>\s?','',line)
            inline(p,bullet[1] if bullet else line)

    for line in md.splitlines():
        if line.startswith('```'):
            flush_table();code=not code;continue
        if not code and line.strip().startswith('|') and line.strip().endswith('|'):
            table_rows.append([cell.strip() for cell in line.strip()[1:-1].split('|')]);continue
        flush_table()
        content_line(line)
    flush_table()
    remaining=[identity for identity in prepared_figures if identity not in placed_figures]
    if remaining:
        document.add_paragraph('研究图表',style='Heading 1')
        for identity in remaining:
            insert_figure(identity)
    out=io.BytesIO()
    document.save(out)
    return out.getvalue()
