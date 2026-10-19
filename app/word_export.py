"""Readable A4 export for the Markdown subset used by stage reports."""
import io
import re

from docx import Document
from docx.shared import Cm, Pt, RGBColor
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


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


def make_docx(md):
    document=Document()
    document.core_properties.author=''
    section=document.sections[0]
    section.page_width,section.page_height=Cm(21),Cm(29.7)
    section.top_margin=section.bottom_margin=Cm(2.2)
    section.left_margin=section.right_margin=Cm(2.2)
    for name in ['Normal','Title','Heading 1','Heading 2','Heading 3','Heading 4','List Bullet','List Number']:
        style=document.styles[name]
        style.font.name='Calibri'
        style.font.color.rgb=RGBColor(0,0,0)
        style.font.underline=False
        style.font.italic=False
        style.font.size=Pt(11)
        style._element.get_or_add_rPr().get_or_add_rFonts().set(qn('w:eastAsia'),'宋体')
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
        style._element.rPr.rFonts.set(qn('w:eastAsia'),'黑体')
        style.paragraph_format.space_before=Pt(12)
        style.paragraph_format.space_after=Pt(7)
        style.paragraph_format.keep_with_next=True
        style.paragraph_format.keep_together=True
    title_used=False
    code=False
    table_rows=[]

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

    for line in md.splitlines():
        if line.startswith('```'):
            flush_table();code=not code;continue
        if not code and line.strip().startswith('|') and line.strip().endswith('|'):
            table_rows.append([cell.strip() for cell in line.strip()[1:-1].split('|')]);continue
        flush_table()
        if not line.strip() or (not code and re.fullmatch(r'[-*_]{3,}',line.strip())):
            continue
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
            # Preserve explicit numbering, including lists starting at values other than 1.
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
    flush_table()
    out=io.BytesIO()
    document.save(out)
    return out.getvalue()
