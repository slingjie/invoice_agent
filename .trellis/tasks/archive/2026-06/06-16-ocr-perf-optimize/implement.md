# OCR 性能优化：执行计划

## 实施步骤

### Step 1: 新增 `SdkOcrProvider`
- [ ] 在 `invoice_agent/ocr.py` 新增 `SdkOcrProvider` 类
- [ ] 实现 `parse(path) → ParsedDocument`
- [ ] 实现 `parse_many(paths, max_workers) → List[ParsedDocument]`
- [ ] 内部用 `asyncio.run()` 包装异步逻辑
- [ ] 用 `asyncio.Semaphore` 控制并发数
- [ ] 映射 `DocParsingResult` → `ParsedDocument`
- [ ] 错误处理：包装 SDK 异常为 `_error_document`

### Step 2: 更新配置
- [ ] `AgentConfig` 新增 `ocr_provider: str = "sdk"` 默认值
- [ ] `run_organize_from_form` 中根据 `config.ocr_provider` 选择 provider

### Step 3: 切换调用方
- [ ] `web.py`: `PaddleAsyncOcrProvider` → `SdkOcrProvider`
- [ ] `cli.py`: `PaddleAsyncOcrProvider` → `SdkOcrProvider`
- [ ] 旧 `PaddleAsyncOcrProvider` 标记 deprecated 注释，保留代码作为 fallback

### Step 4: 更新测试
- [ ] 新增 `SdkOcrProvider` 单元测试（mock SDK 调用）
- [ ] 更新现有测试中的 provider mock
- [ ] 运行 `pytest tests/ -v` 确保全部通过

### Step 5: 手动验证
- [ ] 启动 Web UI：`python -m invoice_agent.cli ui`
- [ ] 用测试发票目录跑一次完整流程
- [ ] 确认预览、编辑、导出均正常
- [ ] 记录 16 张发票的实际耗时

## 验证命令

```bash
# 类型检查（如有）
python -m mypy invoice_agent/ocr.py

# 单元测试
python -m pytest tests/test_invoice_agent.py -v

# 手动计时（CLI 模式）
python -m invoice_agent.cli organize ./测试发票 --trip-info ./测试发票/trip_info.json
```

## 回滚点

每个 Step 完成后可独立回滚：
- Step 1 后：删除 `SdkOcrProvider` 类
- Step 3 后：改回 `PaddleAsyncOcrProvider`
