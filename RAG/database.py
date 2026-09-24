"""
私有数据库构建课程示例
功能：PDF和Excel文档切分、Embedding模型部署、向量检索演示
使用Chroma向量数据库
"""

import os

os.environ['HF_ENDPOINT'] = 'https://hf-mirror.com'

from pathlib import Path
import json
import re
from typing import List, Dict, Iterable, Tuple, Optional
import chromadb
from chromadb.config import Settings
from sentence_transformers import SentenceTransformer, CrossEncoder
import pandas as pd
import pypdf
from bs4 import BeautifulSoup
import torch
from tqdm import tqdm


class PrivateDatabaseBuilder:
    """私有数据库构建器 - 课程演示"""

    rerank_model_path = "cross-encoder/ms-marco-MiniLM-L-6-v2"

    def __init__(self, persist_directory: Optional[str] = None, force_rebuild: bool = False):
        """
        初始化数据库构建器

        Args:
            persist_directory: Chroma数据库持久化目录
            force_rebuild: 是否强制重建数据库（False则使用已有数据库）
        """
        self.force_rebuild = force_rebuild

        if persist_directory is None:
            base_dir = Path(__file__).resolve().parent
            persist_directory = str(base_dir / "chroma_db")

        print("=" * 60)
        print("私有数据库构建系统初始化")

        # 初始化Chroma客户端
        self.chroma_client = chromadb.PersistentClient(path=persist_directory)
        print(f"✅ Chroma数据库初始化完成: {persist_directory}")

        # 检测并设置设备（GPU优先）
        print("\n🔄 检测计算设备...")
        if torch.cuda.is_available():
            self.device = 'cuda'
            gpu_name = torch.cuda.get_device_name(0)
            gpu_memory = torch.cuda.get_device_properties(0).total_memory / 1024**3
            print(f"✅ 检测到GPU: {gpu_name}")
        else:
            self.device = 'cpu'
            print(f"ℹ️  未检测到GPU，使用CPU计算")

        # 加载Embedding模型 (使用中文优化的模型)
        print(f"\n🔄 加载Embedding模型到 {self.device.upper()}...")
        self.embedding_model = SentenceTransformer(
            "sentence-transformers/all-mpnet-base-v2",
            device=self.device
        )
        print("✅ Embedding模型加载完成: all-mpnet-base-v2")

        self.reranker = None
        try:
            self.reranker = CrossEncoder(self.rerank_model_path, device=self.device)
            print("✅ Rerank模型加载完成: bge-reranker-base")
        except Exception as exc:
            print(f"⚠️  Rerank模型加载失败: {exc}")

    def _clean_text(self, text: str) -> str:
        text = text.replace("\r", "\n")
        text = "\n".join(line.strip() for line in text.split("\n"))
        while "\n\n\n" in text:
            text = text.replace("\n\n\n", "\n\n")
        text = self._strip_tail_sections(text)
        return text.strip()

    def _reflow_extracted_pdf_text(self, text: str) -> str:
        """将PDF抽取文本的“软换行”重排成更自然的段落。

        PDF抽取常见问题：每行都有换行，导致段落与句子被打碎。
        这里做保守重排：
        - 空行保持为段落分隔
        - 标题行/列表行尽量保留行结构
        - 其余行在段内合并为空格
        """
        if not text:
            return ""

        lines = [ln.strip() for ln in text.replace("\r", "\n").split("\n")]
        out: List[str] = []
        paragraph: List[str] = []

        heading_re = re.compile(
            r"^(?:\d+(?:\.\d+)*\s+\S+|[IVXLC]+\.?\s+\S+|第[一二三四五六七八九十百千]+[章节篇]\s*\S*)$"
        )
        bullet_re = re.compile(r"^(?:[-*•]\s+|\d+\)\s+|\d+\.\s+|\([a-zA-Z]\)\s+)")

        def flush_paragraph():
            if paragraph:
                out.append(" ".join(paragraph).strip())
                paragraph.clear()

        for line in lines:
            if not line:
                flush_paragraph()
                if out and out[-1] != "":
                    out.append("")
                continue

            # 标题行：尽量独立成段
            if heading_re.match(line):
                flush_paragraph()
                if out and out[-1] != "":
                    out.append("")
                out.append(line)
                out.append("")
                continue

            # 列表/条目：保留为独立行，避免被合并进正文
            if bullet_re.match(line):
                flush_paragraph()
                if out and out[-1] != "":
                    out.append("")
                out.append(line)
                continue

            # 英文断词连字符：上一行以 '-' 结尾且下一行以小写开头
            if paragraph and paragraph[-1].endswith("-") and re.match(r"^[a-z]", line):
                paragraph[-1] = paragraph[-1][:-1] + line
                continue

            paragraph.append(line)

        flush_paragraph()

        rebuilt = "\n".join(out)
        while "\n\n\n" in rebuilt:
            rebuilt = rebuilt.replace("\n\n\n", "\n\n")
        return rebuilt.strip()

    def _normalize_text_input(self, text) -> str:
        if text is None:
            return ""
        if isinstance(text, (list, tuple)):
            text = " ".join(str(item) for item in text if item is not None)
        elif not isinstance(text, str):
            text = str(text)
        return text.strip()

    def _strip_tail_sections(self, text: str) -> str:
        lower = text.lower()
        markers = [
            "\nreferences",
            "\nacknowledgments",
            "\nacknowledgements",
            "\nfunding",
            "\nconflict of interest",
            "\ndisclosure",
        ]
        cut = len(text)
        for marker in markers:
            idx = lower.find(marker)
            if idx != -1:
                cut = min(cut, idx)
        return text[:cut] if cut < len(text) else text

    def _maybe_sentence_split(self, prev_text: str, next_text: str) -> bool:
        prev_text = prev_text.strip()
        next_text = next_text.strip()
        if not prev_text or not next_text:
            return False

        last_char = prev_text[-1]
        first_char = next_text[0]
        if last_char in {".", "!", "?", "\n", "。", "！", "？"}:
            return False

        if re.match(r"[A-Za-z0-9]", last_char) and re.match(r"[A-Za-z0-9]", first_char):
            return True
        if re.match(r"[\u4e00-\u9fff]", last_char) and re.match(r"[\u4e00-\u9fff]", first_char):
            return True
        if not last_char.isspace() and not first_char.isspace():
            return True
        return False

    def report_chunk_stats(self, collection, batch_size: int = 2000):
        total = collection.count()
        if total == 0:
            print("⚠️  向量库为空，无法统计")
            return

        total_length = 0
        total_chunks = 0
        per_source = {}
        global_index = 0

        for offset in range(0, total, batch_size):
            data = collection.get(
                offset=offset,
                limit=batch_size,
                include=["documents", "metadatas"]
            )
            documents = data.get("documents", [])
            metadatas = data.get("metadatas", [])

            for doc, meta in zip(documents, metadatas):
                if not isinstance(doc, str):
                    global_index += 1
                    continue
                text = doc.strip()
                if not text:
                    global_index += 1
                    continue
                total_length += len(text)
                total_chunks += 1

                source = meta.get("source", "") if isinstance(meta, dict) else ""
                start = meta.get("start") if isinstance(meta, dict) else None
                if not isinstance(start, int):
                    start = global_index

                first_char = text[0]
                last_char = text[-1]
                per_source.setdefault(source, []).append((start, first_char, last_char))
                global_index += 1

        avg_size = (total_length / total_chunks) if total_chunks else 0
        print(f"\n📊 Chunk平均大小: {avg_size:.2f} 字符 (总块数: {total_chunks})")

        split_count = 0
        for items in per_source.values():
            items_sorted = sorted(items, key=lambda x: x[0])
            for idx in range(len(items_sorted) - 1):
                prev_last = items_sorted[idx][2]
                next_first = items_sorted[idx + 1][1]
                if self._maybe_sentence_split(prev_last, next_first):
                    split_count += 1

        if split_count > 0:
            print(f"⚠️  疑似存在句子被切分的情况，出现次数: {split_count}")
        else:
            print("✅ 未发现明显的句子被切分情况")

    def _iter_paragraph_spans(self, text: str) -> Iterable[Tuple[int, int]]:
        """按空行分段，返回每段在原文中的 [start, end) span（不丢位置信息）。"""
        cursor = 0
        for m in re.finditer(r"\n{2,}", text):
            start, end = cursor, m.start()
            if text[start:end].strip():
                yield start, end
            cursor = m.end()
        if cursor < len(text) and text[cursor:].strip():
            yield cursor, len(text)

    def _iter_sentence_spans(self, text: str, span_start: int, span_end: int) -> Iterable[Tuple[int, int]]:
        """对指定span做句子级切分，尽量在中英文标点/换行处分句，返回全局 [start, end) span。"""
        block = text[span_start:span_end]
        if not block.strip():
            return

        # 句末标点/换行：尽量让 chunk 边界落在这些位置
        sent_re = re.compile(
            r".+?(?:[。！？!?]+|[；;]+|[。！？!?]+\"|$)",
            flags=re.S,
        )
        local_cursor = 0
        for m in sent_re.finditer(block):
            s, e = m.start(), m.end()
            if e <= local_cursor:
                continue
            piece = block[s:e]
            local_cursor = e
            if piece.strip():
                yield span_start + s, span_start + e

    def _safe_join(self, text: str, start: int, end: int) -> str:
        chunk = text[start:end]
        # 保留原文换行，但移除多余空白行的边界噪声
        return chunk.strip()

    def _dedupe_results(self, ranked):
        seen = set()
        deduped = []
        for item in ranked:
            if self.reranker:
                _, _, document, _, _ = item
            else:
                _, _, document, _ = item

            text = str(document or "").strip().lower()
            key = re.sub(r"\W+", " ", text).strip()[:120]
            if key and key in seen:
                continue
            seen.add(key)
            deduped.append(item)
        return deduped

    def extract_text_from_pdf(self, pdf_path: str) -> str:
        """
        从PDF文件中提取文本

        Args:
            pdf_path: PDF文件路径

        Returns:
            提取的文本内容
        """
        print(f"\n📖 正在读取PDF文件: {os.path.basename(pdf_path)}")

        text_content = []
        with open(pdf_path, 'rb') as file:
            pdf_reader = pypdf.PdfReader(file)
            num_pages = len(pdf_reader.pages)
            print(f"   📄 总页数: {num_pages}")

            # 使用进度条处理每一页
            for page in tqdm(pdf_reader.pages, desc="   ⏳ 提取页面", unit="页", ncols=80):
                text = page.extract_text()
                if text.strip():
                    text_content.append(text)

            raw = "\n".join(text_content)
            reflowed = self._reflow_extracted_pdf_text(raw)
            full_text = self._clean_text(reflowed)
        print(f"✅ PDF文本提取完成，共 {len(full_text)} 个字符")
        return full_text
    
    def extract_text_from_excel(self, xlsx_path: str) -> str:
        """
        从Excel文件中提取文本

        Args:
            xlsx_path: Excel文件路径

        Returns:
            提取的文本内容
        """
        print(f"\n📖 正在读取Excel文件: {os.path.basename(xlsx_path)}")

        parts = []
        try:
            sheets = pd.read_excel(xlsx_path, sheet_name=None)
        except Exception as exc:
            print(f"⚠️  Excel读取失败: {exc}")
            return ""

        desired_cols = [
            "PMID",
            "Title",
            "Authors",
            "Journal",
            "Year",
            "Abstract",
            "Journal Name",
            "Impact Factor",
            "High Impact (IF≥5)",
        ]

        for sheet_name, df in sheets.items():
            if df.empty:
                continue
            parts.append(f"\n[Sheet: {sheet_name}]")
            df = df.fillna("")
            available_cols = [c for c in desired_cols if c in df.columns]
            for _, row in df.iterrows():
                row_items = []
                for col in available_cols:
                    val = str(row.get(col, "")).strip()
                    if val:
                        row_items.append(f"{col}: {val}")
                row_text = " | ".join(row_items)
                if row_text:
                    parts.append(row_text)

        text = self._clean_text("\n".join(parts))
        print(f"✅ Excel文本提取完成，共 {len(text)} 个字符")
        return text

    def extract_text_from_html(self, html_path: str) -> str:
        """
        从HTML文件中提取正文文本（针对公众号文章结构）

        Args:
            html_path: HTML文件路径

        Returns:
            提取的文本内容
        """
        print(f"\n📖 正在读取HTML文件: {os.path.basename(html_path)}")
        try:
            raw = Path(html_path).read_text(encoding="utf-8")
        except Exception:
            raw = Path(html_path).read_text(encoding="utf-8", errors="ignore")

        soup = BeautifulSoup(raw, "html.parser")
        for tag in soup(["script", "style", "noscript"]):
            tag.extract()

        body = soup.find(id="js_content")
        if body is None:
            body = soup.body

        if body:
            text = body.get_text("\n")
        else:
            text = soup.get_text("\n")

        cleaned = self._clean_text(text)
        print(f"✅ HTML文本提取完成，共 {len(cleaned)} 个字符")
        return cleaned

    def _load_sidecar_metadata(self, source_path: Path) -> Dict[str, object]:
        json_path = source_path.with_suffix(".json")
        if not json_path.exists():
            return {}

        try:
            data = json.loads(json_path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"⚠️  读取元数据失败: {json_path.name} ({exc})")
            return {}

        if not isinstance(data, dict):
            return {}

        cleaned: Dict[str, object] = {}
        for key, value in data.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            if isinstance(value, list) and not value:
                continue
            cleaned[key] = value

        return cleaned

    def chunk_text(self, text: str, chunk_size: int = 1100,
                   overlap: int = 220) -> List[Dict[str, any]]:
        """
        将文本切分成小块（Chunking）

        目标：让切片尽量沿“段落/句子边界”切分，并通过滑窗重叠保证上下文连续。
        相比“按字符硬切”，这种方式能显著减少语义断裂与跨句切断。

        Args:
            text: 待切分的文本
            chunk_size: 每块的字符数
            overlap: 块之间的重叠字符数

        Returns:
            包含文本块及其元数据的列表
        """
        normalized = self._normalize_text_input(text)
        if not normalized:
            return []

        print(f"\n✂️  开始文本切分...")
        print(f"   📏 切分参数: 目标块大小≈{chunk_size}字符, 重叠≈{overlap}字符")

        def _is_heading(piece: str) -> bool:
            line = piece.strip()
            if not line:
                return False
            if len(line) > 80:
                return False
            if re.match(r"^\d+(?:\.\d+)*\s+\S+", line):
                return True
            if re.match(r"^[IVXLC]+\.?\s+\S+", line):
                return True
            if re.match(r"^第[一二三四五六七八九十百千]+[章节篇]", line):
                return True
            if line.isupper() and len(line.split()) <= 8:
                return True
            return False

        # 1) 段落 -> 句子 spans（保留原文位置，用于后续邻居拼接）
        sentence_spans: List[Tuple[int, int, bool]] = []
        for p_start, p_end in self._iter_paragraph_spans(normalized):
            for s_start, s_end in self._iter_sentence_spans(normalized, p_start, p_end):
                sentence_spans.append((s_start, s_end, _is_heading(normalized[s_start:s_end])))
        if not sentence_spans:
            sentence_spans = [(0, len(normalized), False)]

        # 2) 句子滑窗拼 chunk：尽量不截断句子
        chunks: List[Dict[str, object]] = []
        chunk_id = 0
        i = 0
        while i < len(sentence_spans):
            chunk_start = sentence_spans[i][0]
            chunk_end = sentence_spans[i][1]

            j = i
            while j + 1 < len(sentence_spans):
                next_is_heading = sentence_spans[j + 1][2]
                if next_is_heading and (chunk_end - chunk_start) >= int(chunk_size * 0.45):
                    # 标题尽量作为新chunk起点，避免跨章节粘连
                    break

                next_end = sentence_spans[j + 1][1]
                if next_end - chunk_start > int(chunk_size * 1.25):
                    break
                if next_end - chunk_start >= chunk_size:
                    chunk_end = next_end
                    j += 1
                    break
                chunk_end = next_end
                j += 1

            text_piece = self._safe_join(normalized, chunk_start, chunk_end)
            if text_piece:
                chunks.append({
                    'id': f"chunk_{chunk_id}",
                    'text': text_piece,
                    'metadata': {
                        'start': int(chunk_start),
                        'end': int(chunk_end),
                        'length': int(len(text_piece)),
                        'chunk_index': int(chunk_id),
                        'chunker': 'sentence_window_v2',
                        'target_chunk_size': int(chunk_size),
                        'overlap': int(overlap),
                    }
                })
                chunk_id += 1

            # 3) 下一窗口：根据 overlap 退回若干句子，保证上下文连续
            if j <= i:
                i += 1
                continue

            # 估算需要回退多少句子达到 overlap 字符
            back = 0
            back_chars = 0
            k = j
            while k >= i and back_chars < overlap:
                s0, s1, _ = sentence_spans[k]
                back_chars += (s1 - s0)
                back += 1
                k -= 1
            i = max(i + 1, j + 1 - max(1, back))

        # 4) 尾部过小块合并，减少“碎片块”
        min_tail = int(max(80, chunk_size * 0.25))
        merged: List[Dict[str, object]] = []
        for c in chunks:
            if not merged:
                merged.append(c)
                continue
            prev = merged[-1]
            if int(c['metadata'].get('length', 0)) < min_tail:
                combined = (str(prev['text']) + "\n\n" + str(c['text'])).strip()
                if len(combined) <= int(chunk_size * 1.6):
                    prev['text'] = combined
                    prev['metadata']['end'] = max(int(prev['metadata']['end']), int(c['metadata']['end']))
                    prev['metadata']['length'] = int(len(prev['text']))
                    continue
            merged.append(c)

        # 4b) 超长块硬切保护：防止极少数段落/句子异常导致单块过长
        hard_max = int(chunk_size * 1.6)
        hard_overlap = int(max(0, min(overlap, int(chunk_size * 0.25))))
        protected: List[Dict[str, object]] = []
        for c in merged:
            try:
                c_start = int(c.get('metadata', {}).get('start', 0))
                c_end = int(c.get('metadata', {}).get('end', c_start))
            except Exception:
                c_start, c_end = 0, 0

            c_text = str(c.get('text', '') or '')
            if len(c_text) <= hard_max or c_end <= c_start:
                protected.append(c)
                continue

            sub_start = c_start
            sub_idx = 0
            while sub_start < c_end:
                sub_end = min(sub_start + hard_max, c_end)
                sub_text = self._safe_join(normalized, sub_start, sub_end)
                if sub_text:
                    meta = dict(c.get('metadata') or {})
                    meta.update({
                        'start': int(sub_start),
                        'end': int(sub_end),
                        'length': int(len(sub_text)),
                        'hard_cut': True,
                        'hard_cut_max_chars': int(hard_max),
                        'hard_cut_index': int(sub_idx),
                    })
                    protected.append({
                        'id': f"{c.get('id', 'chunk')}_hard_{sub_idx}",
                        'text': sub_text,
                        'metadata': meta,
                    })
                    sub_idx += 1

                next_start = sub_end - hard_overlap
                if next_start <= sub_start:
                    next_start = sub_end
                sub_start = next_start

        # 重新编号 chunk_index（保证邻居拼接可用）
        final_chunks = protected
        for idx, c in enumerate(final_chunks):
            c['id'] = f"chunk_{idx}"
            c['metadata']['chunk_index'] = int(idx)
            c['metadata']['chunk_count'] = int(len(final_chunks))

        print(f"✅ 文本切分完成，共生成 {len(final_chunks)} 个文本块")
        return final_chunks

    def create_embeddings(self, texts: List[str]) -> List[List[float]]:
        """
        为文本列表生成Embedding向量

        Args:
            texts: 文本列表

        Returns:
            Embedding向量列表
        """
        print(f"\n🔄 生成Embedding向量（使用{self.device.upper()}）...")
        print(f"   📊 待处理文本数量: {len(texts)}")

        embeddings = self.embedding_model.encode(
            texts,
            show_progress_bar=True,
            batch_size=32,
            normalize_embeddings=True
        )

        print(f"✅ Embedding生成完成")
        print(f"   🔢 向量维度: {len(embeddings[0])}")
        return embeddings.tolist()

    def create_collection(self, collection_name: str = "course_documents"):
        """
        创建或获取向量集合
        Args:
            collection_name: 集合名称

        Returns:
            Chroma集合对象
        """
        print(f"\n📦 创建/获取向量集合: {collection_name}")

        # 检查集合是否存在
        existing_collections = [col.name for col in self.chroma_client.list_collections()]

        if collection_name in existing_collections:
            if self.force_rebuild:
                print(f"⚠️  集合已存在，将被覆盖（force_rebuild=True）")
                self.chroma_client.delete_collection(collection_name)
            else:
                print(f"✅ 集合已存在，使用现有数据（{self.chroma_client.get_collection(collection_name).count()}个向量）")
                print(f"   提示: 如需重建，设置 force_rebuild=True")
                return self.chroma_client.get_collection(collection_name)

        collection = self.chroma_client.get_or_create_collection(
            name=collection_name,
            metadata={"hnsw:space": "cosine", "description": "课程文档向量数据库"}
        )

        print(f"✅ 集合准备完成")
        return collection

    def add_to_collection(self, collection, chunks: List[Dict],
                         source_file: str, extra_metadata: Optional[Dict[str, object]] = None):
        """
        将文本块添加到向量集合

        Args:
            collection: Chroma集合对象
            chunks: 文本块列表
            source_file: 源文件名
        """
        print(f"\n💾 添加数据到向量集合...")
        print(f"   📄 源文件: {source_file}")
        print(f"   📊 文本块数量: {len(chunks)}")

        ids = []
        texts = []
        metadatas = []
        for chunk in chunks:
            text = self._normalize_text_input(chunk.get('text'))
            if not text:
                continue

            ids.append(f"{source_file}_{chunk['id']}")
            texts.append(text)
            metadata = chunk.get('metadata') or {}
            if 'chunk_index' not in metadata:
                # 兼容旧数据结构
                try:
                    metadata['chunk_index'] = int(str(chunk.get('id', '')).split('_')[-1])
                except Exception:
                    metadata['chunk_index'] = None
            merged = {'source': source_file, **metadata}
            if extra_metadata:
                merged.update(extra_metadata)
            metadatas.append(merged)

        if not texts:
            print("⚠️  该文件无可用文本块，跳过入库")
            return

        cleaned_ids = []
        cleaned_texts = []
        cleaned_metadatas = []
        skipped = 0
        for chunk_id, text, metadata in zip(ids, texts, metadatas):
            normalized = self._normalize_text_input(text)
            if not normalized:
                skipped += 1
                continue
            cleaned_ids.append(chunk_id)
            cleaned_texts.append(normalized)
            cleaned_metadatas.append(metadata)

        if skipped:
            print(f"   ⚠️  已跳过 {skipped} 个空或非法文本块")

        if not cleaned_texts:
            print("⚠️  该文件无可用文本块，跳过入库")
            return

        ids = cleaned_ids
        texts = cleaned_texts
        metadatas = cleaned_metadatas

        # 生成embeddings（带进度条）
        print(f"\n   🔄 生成向量（{self.device.upper()}）...")
        try:
            embeddings = self.embedding_model.encode(
                texts,
                show_progress_bar=True,
                batch_size=32,
                normalize_embeddings=True
            ).tolist()
        except TypeError as exc:
            print(f"⚠️  批量编码失败，尝试逐条编码: {exc}")
            embeddings = []
            safe_ids = []
            safe_texts = []
            safe_metadatas = []
            for chunk_id, text, metadata in zip(ids, texts, metadatas):
                try:
                    emb = self.embedding_model.encode(
                        [text],
                        normalize_embeddings=True
                    )
                except Exception as item_exc:
                    print(f"   ⚠️  跳过不可编码文本块: {item_exc}")
                    continue
                embeddings.append(emb[0].tolist())
                safe_ids.append(chunk_id)
                safe_texts.append(text)
                safe_metadatas.append(metadata)

            if not embeddings:
                print("⚠️  该文件无可用文本块，跳过入库")
                return

            ids = safe_ids
            texts = safe_texts
            metadatas = safe_metadatas

        # 添加到集合 (按批次避免超过上限)
        print(f"   💾 存储到数据库...")
        max_batch = 4000
        total = len(ids)
        for start in range(0, total, max_batch):
            end = min(start + max_batch, total)
            collection.add(
                ids=ids[start:end],
                embeddings=embeddings[start:end],
                documents=texts[start:end],
                metadatas=metadatas[start:end]
            )

        print(f"\n✅ 成功添加 {len(texts)} 个向量到集合")

    def process_directory(self, data_dir: Optional[str] = None):
        """
        处理整个数据目录

        Args:
            data_dir: 数据目录路径
        """
        print("\n" + "=" * 60)
        print("开始处理数据目录")

        if data_dir is None:
            base_dir = Path(__file__).resolve().parent
            data_dir = str(base_dir / "sci_pdf")

        data_path = Path(data_dir)

        # 统计文件数量
        pdf_files = list((data_path / "pdf").glob("*.pdf")) if (data_path / "pdf").exists() else []
        md_files = list((data_path / "md").glob("*.md")) if (data_path / "md").exists() else []
        excel_files = list((data_path / "excel").glob("*.xlsx")) if (data_path / "excel").exists() else []
        html_files = list((data_path / "html").glob("*.html")) if (data_path / "html").exists() else []
        total_files = len(pdf_files) + len(md_files) + len(excel_files) + len(html_files)

        if total_files == 0:
            print("⚠️  未找到任何数据文件")
            return None

        print(f"\n📊 发现 {total_files} 个文件:")
        print(f"   PDF文件: {len(pdf_files)} 个")
        print(f"   Excel文件: {len(excel_files)} 个")
        print(f"   HTML文件: {len(html_files)} 个")

        collection = self.create_collection("course_documents")

        # 如果使用现有数据库，跳过处理
        if not self.force_rebuild and collection.count() > 0:
            return collection

        total_chunks = 0
        file_progress = tqdm(total=total_files, desc="📁 处理文件", unit="文件", ncols=80)

        # 处理PDF文件
        if pdf_files:
            print(f"\n📁 处理PDF目录: {data_path / 'pdf'}")
            for pdf_file in pdf_files:
                print(f"\n{'─' * 60}")
                text = self.extract_text_from_pdf(str(pdf_file))
                chunks = self.chunk_text(text)
                self.add_to_collection(collection, chunks, pdf_file.name)
                total_chunks += len(chunks)
                file_progress.update(1)


        if excel_files:
            print(f"\n📁 处理Excel目录: {data_path / 'excel'}")
            for xlsx_file in excel_files:
                print(f"\n{'─' * 60}")
                text = self.extract_text_from_excel(str(xlsx_file))
                if not text:
                    file_progress.update(1)
                    continue
                chunks = self.chunk_text(text)
                self.add_to_collection(collection, chunks, xlsx_file.name)
                total_chunks += len(chunks)
                file_progress.update(1)

        if html_files:
            print(f"\n📁 处理HTML目录: {data_path / 'html'}")
            for html_file in html_files:
                print(f"\n{'─' * 60}")
                text = self.extract_text_from_html(str(html_file))
                if not text:
                    file_progress.update(1)
                    continue
                chunks = self.chunk_text(text)
                sidecar = self._load_sidecar_metadata(html_file)
                self.add_to_collection(collection, chunks, html_file.name, extra_metadata=sidecar)
                total_chunks += len(chunks)
                file_progress.update(1)

        file_progress.close()

        print(f"\n{'=' * 60}")
        print(f"数据处理完成！总计添加 {total_chunks} 个向量")

        return collection

    def search(self, collection, query: str, n_results: int = 5):
        """
        向量检索演示

        Args:
            collection: Chroma集合对象
            query: 查询文本
            n_results: 返回结果数量
        """
        print(f"\n{'=' * 60}")
        print(f"🔍 向量检索")
        print(f"\n❓ 查询问题: {query}")

        # 生成查询向量
        query_embedding = self.embedding_model.encode(
            [query],
            normalize_embeddings=True
        ).tolist()

        retrieve_k = max(n_results, 30)
        rerank_top_k = 5

        # 执行初检索
        results = collection.query(
            query_embeddings=query_embedding,
            n_results=retrieve_k
        )

        ids = results['ids'][0]
        distances = results['distances'][0]
        documents = results['documents'][0]
        metadatas = results['metadatas'][0]

        if self.reranker:
            pairs = [(query, doc) for doc in documents]
            scores = self.reranker.predict(pairs)
            ranked = sorted(
                zip(ids, distances, documents, metadatas, scores),
                key=lambda x: x[4],
                reverse=True,
            )[:rerank_top_k]
        else:
            ranked = list(zip(ids, distances, documents, metadatas))[:rerank_top_k]

        ranked = self._dedupe_results(ranked)[:rerank_top_k]

        def _neighbor_ids(source: str, chunk_index: Optional[int]) -> List[str]:
            if not source or chunk_index is None:
                return []
            try:
                idx = int(chunk_index)
            except Exception:
                return []
            # 取前后各1块，拼回上下文
            return [f"{source}_chunk_{idx-1}", f"{source}_chunk_{idx}", f"{source}_chunk_{idx+1}"]

        print(f"\n✅ 检索到 {len(ranked)} 个相关结果（已自动拼接相邻上下文）:\n")

        for i, item in enumerate(ranked, 1):
            if self.reranker:
                doc_id, distance, document, metadata, score = item
                print(f"📌 结果 #{i}")
                print(f"   📊 Rerank得分: {score:.4f}")
            else:
                doc_id, distance, document, metadata = item
                similarity = (1 - distance) * 100  # 转换为相似度百分比
                print(f"📌 结果 #{i}")
                print(f"   📊 相似度: {similarity:.2f}%")

            source = metadata.get('source') if isinstance(metadata, dict) else None
            chunk_index = metadata.get('chunk_index') if isinstance(metadata, dict) else None
            print(f"   📄 来源: {source}")

            expanded = document
            neighbor_ids = _neighbor_ids(source, chunk_index)
            if neighbor_ids:
                try:
                    neighbor_docs: List[str] = []
                    for nid in neighbor_ids:
                        try:
                            got = collection.get(ids=[nid], include=["documents"]) or {}
                            docs = got.get("documents") or []
                            if docs and isinstance(docs[0], str) and docs[0].strip():
                                neighbor_docs.append(docs[0].strip())
                        except Exception:
                            continue
                    if neighbor_docs:
                        expanded = "\n\n".join(neighbor_docs)
                except Exception:
                    pass

            print(f"   📝 内容片段(扩展上下文):")
            print(f"   {expanded[:350]}...")
            print()


def main():
    """主函数"""
    base_dir = Path(__file__).resolve().parent
    default_db_dir = base_dir / "chroma_db"
    default_data_dir = base_dir / "sci_pdf"

    # 询问是否重建数据库
    print("📦 检测到已有的数据库文件" if default_db_dir.exists() else "🆕 首次运行")
    print("\n请选择运行模式:")
    print("  1. 使用现有数据库")
    print("  2. 重建数据库（用于数据更新）")
    print()

    choice = input("请输入选项 (1 或 2，默认1): ").strip()
    force_rebuild = (choice == "2")
    
    print()

    # 创建构建器实例
    builder = PrivateDatabaseBuilder(
        persist_directory=str(default_db_dir),
        force_rebuild=force_rebuild
    )

    # 处理数据目录
    collection = builder.process_directory(str(default_data_dir))

    if collection is None:
        print("未生成向量库，跳过检索示例。请检查 ./syt/sci_pdf 是否有可用文件。")
        return

    if not force_rebuild:
        builder.report_chunk_stats(collection)
    
    english_queries = [
        "What are the main findings about functional connectivity differences in ASD?",
        "Which brain networks show atypical connectivity in ASD and how are they measured?",
        "What EEG-based biomarkers or features are reported for ASD classification?",
        "What machine learning methods are used for ASD diagnosis in these papers?",
        "Do these studies report sex differences in ASD, and if so, what are they?",
        "What datasets or cohorts are used in these ASD studies?"
    ]

    for query in english_queries:
        print(f"\n{'='*60}")
        print(f"❓ English Query: {query}")
        builder.search(collection, query, n_results=3)
    
if __name__ == "__main__":
    main()
