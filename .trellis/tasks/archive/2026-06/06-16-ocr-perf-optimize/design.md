# OCR 性能优化：技术设计

## 架构概览

```
当前:  web.py (Thread) → pipeline.py → PaddleAsyncOcrProvider
                                          ├── urllib.request (提交)
                                          ├── requests.Session (轮询/下载)
                                          └── ThreadPoolExecutor (并发)

改造后: web.py (Thread) → pipeline.py → SdkOcrProvider
                                          └── asyncio.run(AsyncPaddleOCRClient)
                                               ├── httpx (HTTP/2, 连接池)
                                               ├── asyncio.gather (并发提交+轮询)
                                               └── AsyncPoller (指数退避)
```

## 核心设计

### 1. 新增 `SdkOcrProvider`

新建类替换 `PaddleAsyncOcrProvider`，实现相同的 `parse()/parse_many()` 接口：

```python
class SdkOcrProvider:
    def parse(self, path: Path) -> ParsedDocument:
        return self.parse_many([path], max_workers=1)[0]

    def parse_many(self, paths: List[Path], max_workers: int = 3) -> List[ParsedDocument]:
        return asyncio.run(self._parse_many_async(paths, max_workers))

    async def _parse_many_async(self, paths, max_workers):
        # 用 asyncio.Semaphore 控制并发
        # asyncio.gather 并发提交 + 等待
        ...
```

关键点：`asyncio.run()` 在调用线程（即 web.py 的后台线程）中创建新的事件循环，官方支持此用法。

### 2. 并发控制

SDK 的 `AsyncPaddleOCRClient` 内部用 `httpx` 连接池管理并发，我们外层用 `asyncio.Semaphore(max_workers)` 限制同时进行的 job 数，避免触发 API 限流。

### 3. 结果映射

SDK 的 `DocParsingResult` → 我们的 `ParsedDocument`：

```python
def _to_parsed_document(path: Path, result: DocParsingResult) -> ParsedDocument:
    texts = [page.markdown_text for page in result.pages if page.markdown_text]
    raw_text = "\n\n".join(texts)
    return ParsedDocument(
        source_path=path,
        raw_text=raw_text,
        raw_result={"job_id": result.job_id, "pages": [...]},
        fields=extract_fields_from_text(raw_text, path),
        ok=True,
    )
```

### 4. 进度回调兼容

当前 `_parse_records` 每完成一个文件就调 `progress_callback(record)`。SDK 异步模式下需要等 `asyncio.gather` 全部返回后才能拿到结果进度。

方案：保留 `progress_callback` 参数。在 `asyncio.gather` 中使用 `asyncio.as_completed` 替代，每完成一个就回调。伪代码：

```python
async def _parse_many_async(self, paths, max_workers, progress_callback=None):
    sem = asyncio.Semaphore(max_workers)
    async def process_one(path):
        async with sem:
            async with AsyncPaddleOCRClient(...) as client:
                return await client.parse_document(file_path=path)
    
    tasks = [asyncio.create_task(process_one(p)) for p in paths]
    results = []
    for coro in asyncio.as_completed(tasks):
        result = await coro
        results.append(result)
    return results
```

注意：`progress_callback` 目前只在 `organize_folder` 的串行路径（workers=1）和非 `parse_many` 路径使用。`parse_many` 路径（有 `parse_many` 方法时）不传 callback。所以进度回调暂时保持现状即可，不影响功能。

### 5. 旧 Provider 处理

- `PaddleAsyncOcrProvider` → 标记 deprecated，保留作为 fallback
- `PaddleOcrProvider`（同步 API）→ 保持不变，CLI 等场景可能还在用
- 新增配置项 `ocr_provider: "sdk"` 作为默认值

### 6. CLI 兼容

`cli.py` 也使用 `PaddleAsyncOcrProvider`，同样受益于 SDK 切换。

## 文件改动

| 文件 | 改动 |
|------|------|
| `invoice_agent/ocr.py` | 新增 `SdkOcrProvider`；`PaddleAsyncOcrProvider` 标记 deprecated |
| `invoice_agent/web.py` | `run_organize_from_form` 中 `PaddleAsyncOcrProvider` → `SdkOcrProvider` |
| `invoice_agent/cli.py` | 同上 |
| `invoice_agent/config.py` | 新增 `ocr_provider: "sdk"` 默认值 |
| `invoice_agent/pipeline.py` | 无改动（接口兼容） |
| `tests/test_invoice_agent.py` | 更新 OCR mock，新增 SDK provider 测试 |

## 风险与回滚

- **风险1**：`asyncio.run()` 在已有事件循环的线程中报错 → web.py 的后台线程是裸 `threading.Thread`，没有事件循环，安全
- **风险2**：SDK 的 `httpx` 与现有 `requests` 依赖冲突 → 两个库独立，不冲突
- **回滚**：将 provider 改回 `PaddleAsyncOcrProvider` 即可，下游零改动
