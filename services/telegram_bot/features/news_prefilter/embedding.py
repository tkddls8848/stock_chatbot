"""bge-m3(ONNX int8) 문장 임베딩. 임베딩 one-shot 프로세스에서만 import한다.

봇 프로세스는 이 모듈을 import하지 않는다 — 봇 유닛은 `MemoryMax=1G`이고 모델은 혼자 1GB를 쓴다
(실측 2026-11-28). onnxruntime·tokenizers도 여기서만 쓴다.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np

MAX_TOKENS = 128


class Encoder:
    def __init__(self, model_dir: Path, *, threads: int = 1):
        import onnxruntime as ort
        from tokenizers import Tokenizer

        options = ort.SessionOptions()
        # 1스레드가 CPU초당 처리량이 가장 높다(실측: 2스레드는 벽시계 6% 단축에 CPU 83% 증가).
        options.intra_op_num_threads = threads
        options.inter_op_num_threads = 1
        self._session = ort.InferenceSession(
            str(model_dir / "model.onnx"), options, providers=["CPUExecutionProvider"],
        )
        self._tokenizer = Tokenizer.from_file(str(model_dir / "tokenizer.json"))
        self._tokenizer.enable_truncation(MAX_TOKENS)
        self._tokenizer.enable_padding()
        self._inputs = {item.name for item in self._session.get_inputs()}

    def encode(self, texts: list[str]) -> np.ndarray:
        """L2 정규화한 [CLS] 벡터(float32, len(texts) × 1024)."""
        if not texts:
            return np.zeros((0, 0), dtype=np.float32)
        encoded = self._tokenizer.encode_batch(texts)
        ids = np.array([item.ids for item in encoded], dtype=np.int64)
        mask = np.array([item.attention_mask for item in encoded], dtype=np.int64)
        feed = {"input_ids": ids, "attention_mask": mask}
        if "token_type_ids" in self._inputs:
            feed["token_type_ids"] = np.zeros_like(ids)
        hidden = self._session.run(None, feed)[0][:, 0]
        norms = np.linalg.norm(hidden, axis=1, keepdims=True)
        return (hidden / np.maximum(norms, 1e-12)).astype(np.float32)
