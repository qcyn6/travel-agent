import httpx

from backend.observability import observe_external_request


class EmbeddingConfigurationError(RuntimeError):
    pass


class EmbeddingError(RuntimeError):
    pass


class OpenAICompatibleEmbeddingClient:
    def __init__(
        self,
        api_key: str | None,
        *,
        base_url: str,
        model: str,
        dimensions: int = 1024,
        timeout_seconds: float = 30.0,
        batch_size: int = 10,
        client=None,
    ):
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.dimensions = dimensions
        self.timeout_seconds = timeout_seconds
        self.batch_size = max(1, min(batch_size, 10))
        self.client = client

    async def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise EmbeddingConfigurationError("未配置 EMBEDDING_API_KEY")

        vectors: list[list[float]] = []
        for start in range(0, len(texts), self.batch_size):
            vectors.extend(await self._embed_batch(texts[start : start + self.batch_size]))
        return vectors

    async def _embed_batch(self, texts: list[str]) -> list[list[float]]:
        payload = {
            "model": self.model,
            "input": texts,
            "dimensions": self.dimensions,
            "encoding_format": "float",
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            async with observe_external_request("dashscope", "embedding"):
                if self.client is not None:
                    response = await self.client.post(
                        f"{self.base_url}/embeddings", json=payload, headers=headers
                    )
                else:
                    async with httpx.AsyncClient(timeout=self.timeout_seconds) as client:
                        response = await client.post(
                            f"{self.base_url}/embeddings", json=payload, headers=headers
                        )
        except httpx.RequestError as exc:
            raise EmbeddingError("Embedding 服务网络请求失败") from exc
        if response.status_code != 200:
            raise EmbeddingError(f"Embedding 服务返回 HTTP {response.status_code}")

        try:
            body = response.json()
            data = sorted(body["data"], key=lambda item: item["index"])
            batch = [item["embedding"] for item in data]
        except (ValueError, KeyError, TypeError) as exc:
            raise EmbeddingError("Embedding 服务返回了无效数据") from exc
        if len(batch) != len(texts) or any(len(vector) != self.dimensions for vector in batch):
            raise EmbeddingError("Embedding 返回数量或向量维度不正确")
        return batch
