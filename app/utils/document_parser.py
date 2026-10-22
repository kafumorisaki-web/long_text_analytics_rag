import io
import re
import charset_normalizer
from docx import Document
import pymupdf
import ebooklib
from ebooklib import epub
from bs4 import BeautifulSoup


class UniversalDocumentParser:
    @classmethod
    def parse_file(cls, file_bytes: bytes, file_name: str) -> str:

        ext = file_name.split('.')[-1].lower()
        if ext == "txt":
            raw_text = cls._parse_txt(file_bytes)
        elif ext == "docx":
            raw_text = cls._parse_docx(file_bytes)
        elif ext == "pdf":
            raw_text = cls._parse_pdf(file_bytes)
        elif ext == "epub":
            raw_text = cls._parse_epub(file_bytes)
        else:
            raise ValueError(f"不受支持的文件格式: {ext}")

        # 把所有连续超过三个的换行符压扁成标准的双换行
        return re.sub(r'\n{3,}', '\n\n', raw_text)

    @staticmethod
    def _parse_txt(file_bytes: bytes) -> str:
        # 强转为内部标准字符串
        detection = charset_normalizer.from_bytes(file_bytes).best()
        if detection is None:
            return file_bytes.decode("utf-8", errors="ignore")
        return str(detection)

    @staticmethod
    def _parse_docx(file_bytes: bytes) -> str:
        # DOCX 底层是打包的 XML，直接遍历段落节点
        doc = Document(io.BytesIO(file_bytes))
        paragraphs = [p.text.strip() for p in doc.paragraphs if p.text.strip()]
        return "\n\n".join(paragraphs)

    @staticmethod
    def _parse_pdf(file_bytes: bytes) -> str:
        # PDF 底层没有段落概念，只有坐标。提取排版区块(blocks)来近似自然段
        doc = pymupdf.open(stream=file_bytes, filetype="pdf")
        paragraphs = []
        for page in doc:
            blocks = page.get_text("blocks")
            for b in blocks:
                text = b[4].strip()  # block 元组的索引 4 是文本内容
                if text:
                    # 如果一行的结尾不是句号、叹号等结束语，与后面的回车缝合在一起
                    text = re.sub(r'([^.!?。！？\n])\n([^\n])', r'\1\2', text)
                    paragraphs.append(text)
        return "\n\n".join(paragraphs)

    @staticmethod
    def _parse_epub(file_bytes: bytes) -> str:
        # EPUB 本质是 HTML 压缩包，解压后用 BS4 剥离所有前端标签，只留纯文本
        book = epub.read_epub(io.BytesIO(file_bytes))
        text_blocks = []
        for item in book.get_items():
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                soup = BeautifulSoup(item.get_content(), "html.parser")
                paragraphs = [p.get_text().strip() for p in soup.find_all(["p", "h1", "h2", "h3"]) if
                              p.get_text().strip()]
                if paragraphs:
                    text_blocks.append("\n\n".join(paragraphs))
        return "\n\n".join(text_blocks)
