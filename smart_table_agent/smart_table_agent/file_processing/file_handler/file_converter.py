import os
import pandas as pd
from typing import Optional
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from langchain_community.document_loaders import PyPDFLoader, UnstructuredWordDocumentLoader, TextLoader


class FileConverter:
    """
    文件转换类：将不同格式的文件转换为其他格式
    """

    @staticmethod
    def excel_to_csv(excel_path: str, csv_path: Optional[str] = None) -> str:
        """
        Excel 文件转换为 CSV
        """
        if not os.path.isfile(excel_path):
            raise ValueError(f"文件不存在: {excel_path}")
        ext = os.path.splitext(excel_path)[1].lower()
        if ext not in [".xls", ".xlsx"]:
            raise ValueError(f"不是 Excel 文件: {excel_path}")

        df = pd.read_excel(excel_path)
        if csv_path is None:
            csv_path = os.path.splitext(excel_path)[0] + ".csv"
        df.to_csv(csv_path, index=False)
        return csv_path

    @staticmethod
    def csv_to_excel(csv_path: str, excel_path: Optional[str] = None) -> str:
        """
        CSV 文件转换为 Excel
        """
        if not os.path.isfile(csv_path):
            raise ValueError(f"文件不存在: {csv_path}")
        ext = os.path.splitext(csv_path)[1].lower()
        if ext != ".csv":
            raise ValueError(f"不是 CSV 文件: {csv_path}")

        df = pd.read_csv(csv_path)
        if excel_path is None:
            excel_path = os.path.splitext(csv_path)[0] + ".xlsx"
        df.to_excel(excel_path, index=False)
        return excel_path

    @staticmethod
    def pdf_to_txt(pdf_path: str, txt_path: Optional[str] = None) -> str:
        """
        PDF 文件转换为 TXT
        """
        if not os.path.isfile(pdf_path):
            raise ValueError(f"文件不存在: {pdf_path}")
        ext = os.path.splitext(pdf_path)[1].lower()
        if ext != ".pdf":
            raise ValueError(f"不是 PDF 文件: {pdf_path}")

        loader = PyPDFLoader(pdf_path)
        docs = loader.load()
        content = "\n".join([d.page_content for d in docs])

        if txt_path is None:
            txt_path = os.path.splitext(pdf_path)[0] + ".txt"

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)

        return txt_path

    @staticmethod
    def txt_to_excel(txt_path: str, excel_path: Optional[str] = None, delimiter: str = "\t") -> str:
        """
        TXT 文件转换为 Excel（按分隔符拆列，默认制表符）
        """
        if not os.path.isfile(txt_path):
            raise ValueError(f"文件不存在: {txt_path}")
        ext = os.path.splitext(txt_path)[1].lower()
        if ext != ".txt":
            raise ValueError(f"不是 TXT 文件: {txt_path}")

        df = pd.read_csv(txt_path, sep=delimiter)
        if excel_path is None:
            excel_path = os.path.splitext(txt_path)[0] + ".xlsx"
        df.to_excel(excel_path, index=False)
        return excel_path

    @staticmethod
    def docx_to_txt(docx_path: str, txt_path: Optional[str] = None) -> str:
        """
        Word 文件转换为 TXT
        """
        if not os.path.isfile(docx_path):
            raise ValueError(f"文件不存在: {docx_path}")
        ext = os.path.splitext(docx_path)[1].lower()
        if ext != ".docx":
            raise ValueError(f"不是 Word 文件: {docx_path}")

        loader = UnstructuredWordDocumentLoader(docx_path)
        docs = loader.load()
        content = "\n".join([d.page_content for d in docs])

        if txt_path is None:
            txt_path = os.path.splitext(docx_path)[0] + ".txt"

        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(content)

        return txt_path

    @staticmethod
    def txt_to_txt(txt_path: str, txt_out_path: Optional[str] = None) -> str:
        """
        直接复制 TXT 文件
        """
        if not os.path.isfile(txt_path):
            raise ValueError(f"文件不存在: {txt_path}")
        if txt_out_path is None:
            txt_out_path = os.path.splitext(txt_path)[0] + "_copy.txt"
        with open(txt_path, "r", encoding="utf-8") as f:
            content = f.read()
        with open(txt_out_path, "w", encoding="utf-8") as f:
            f.write(content)
        return txt_out_path

    @staticmethod
    def docx_to_pdf(docx_path: str, pdf_path: str = None) -> str:
        """
        Word (.docx) 文件转换为 PDF
        """
        # pip install docx2pdf
        from docx2pdf import convert

        if not os.path.isfile(docx_path):
            raise ValueError(f"文件不存在: {docx_path}")
        ext = os.path.splitext(docx_path)[1].lower()
        if ext != ".docx":
            raise ValueError(f"不是 Word 文件: {docx_path}")

        if pdf_path is None:
            pdf_path = os.path.splitext(docx_path)[0] + ".pdf"

        # docx2pdf convert 可以指定输出文件
        convert(docx_path, pdf_path)
        return pdf_path

    @staticmethod
    def excel_to_pdf(excel_path: str, pdf_path: str = None) -> str:
        """
        Excel 文件转换为 PDF
        """
        if not os.path.isfile(excel_path):
            raise ValueError(f"文件不存在: {excel_path}")
        ext = os.path.splitext(excel_path)[1].lower()
        if ext not in [".xls", ".xlsx"]:
            raise ValueError(f"不是 Excel 文件: {excel_path}")

        if pdf_path is None:
            pdf_path = os.path.splitext(excel_path)[0] + ".pdf"

        # 读取 Excel
        df = pd.read_excel(excel_path)

        # 保存为 PDF
        with PdfPages(pdf_path) as pdf:
            fig, ax = plt.subplots(figsize=(8, len(df) / 2))
            ax.axis('off')
            table = ax.table(cellText=df.values, colLabels=df.columns, loc='center')
            table.auto_set_font_size(False)
            table.set_fontsize(10)
            table.scale(1, 1.5)
            pdf.savefig(fig, bbox_inches='tight')
            plt.close(fig)

        return pdf_path


if __name__ == '__main__':
    converter = FileConverter()

    # 方法 A：跨平台
    pdf_path = converter.excel_to_pdf("data/example.xlsx")
    print("生成 PDF:", pdf_path)

    # 方法 B：Windows COM
    # pdf_path_win = excel_to_pdf_windows("data/example.xlsx")
    # print("生成 PDF:", pdf_path_win)
