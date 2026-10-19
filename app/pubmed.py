"""Bounded NCBI E-utilities metadata retrieval; no full-text claims."""
import json
from urllib.parse import urlencode
from urllib.request import Request,urlopen
import xml.etree.ElementTree as ET

BASE='https://eutils.ncbi.nlm.nih.gov/entrez/eutils/'


def fetch(endpoint,parameters):
    url=BASE+endpoint+'?'+urlencode({**parameters,'tool':'YanxuResearchStudio'})
    with urlopen(Request(url,headers={'User-Agent':'YanxuResearchStudio/0.2.0'}),timeout=25) as response:
        return response.read(5*1024*1024)


def retrieve(query,journal_only=True):
    term='('+query+')'+(' AND "Journal Article"[Publication Type]' if journal_only else '')
    try:
        search=json.loads(fetch('esearch.fcgi',dict(db='pubmed',term=term,retmode='json',retmax=50,sort='relevance')))
        if 'error' in search or 'error' in search.get('esearchresult',{}):raise ValueError('search failed')
        result=search['esearchresult'];ids=result['idlist'];total=int(result['count'])
        if not ids:return [],total
        root=ET.fromstring(fetch('efetch.fcgi',dict(db='pubmed',id=','.join(ids),retmode='xml')))
        entries=[]
        def text(node):return ' '.join(''.join(node.itertext()).split()) if node is not None else ''
        for record in root.findall('PubmedArticle'):
            article=record.find('./MedlineCitation/Article')
            if article is None:continue
            title=text(article.find('ArticleTitle'));pmid=record.findtext('./MedlineCitation/PMID','')
            if not title or not pmid:continue
            doi=next((text(x) for x in record.findall('./PubmedData/ArticleIdList/ArticleId') if x.get('IdType')=='doi'),'')
            date=article.find('./Journal/JournalIssue/PubDate')
            year=text(date.find('Year')) if date is not None else ''
            if not year and date is not None:year=text(date.find('MedlineDate'))[:4]
            authors=[]
            for author in article.findall('./AuthorList/Author')[:12]:
                authors.append((text(author.find('ForeName'))+' '+text(author.find('LastName'))).strip() or text(author.find('CollectiveName')))
            abstract='\n'.join((x.get('Label','')+': ' if x.get('Label') else '')+text(x) for x in article.findall('./Abstract/AbstractText'))
            types=[text(x) for x in article.findall('./PublicationTypeList/PublicationType')]
            entries.append(dict(id=doi or 'pmid:'+pmid,title=title,pmid=pmid,doi=doi,
                url='https://pubmed.ncbi.nlm.nih.gov/'+pmid+'/',authors=authors,year=int(year) if year.isdigit() else None,
                journal=text(article.find('./Journal/Title')),issue=text(article.find('./Journal/JournalIssue/Issue')),
                abstract=abstract,type='journal-article' if 'Journal Article' in types else 'other',
                publication_types=types,is_supplement=False,source='PubMed',status='metadata_only',enabled=False,query=query))
        return entries,total
    except Exception:
        raise ValueError('PubMed 检索暂时不可用，请稍后重试或导入数据库导出文件；未保存不完整结果。') from None
