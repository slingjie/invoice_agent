from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

from .config import load_agent_config
from .models import TripInfo
from .ocr import SdkOcrProvider
from .pipeline import ORGANIZE_MODE_BATCH_SUBFOLDERS, ORGANIZE_MODE_SINGLE, organize_batch_subfolders, organize_folder
from .trip_audit import TripAuditPolicy
from .web import run_ui


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="invoice_agent", description="整理一次出差的发票与行程单")
    subparsers = parser.add_subparsers(dest="command", required=True)
    organize = subparsers.add_parser("organize", help="识别文件夹并生成报销清单")
    organize.add_argument("folder", type=Path, help="出差发票文件夹")
    organize.add_argument("--trip-info", type=Path, help="trip_info.json 路径")
    organize.add_argument("--config", type=Path, help="invoice_agent_config.json 路径，保存 PaddleOCR API 配置")
    organize.add_argument("--out-dir", type=Path, help="输出目录；默认在源文件夹内创建整理结果目录")
    organize.add_argument(
        "--mode",
        choices=["single", "batch-subfolders"],
        default="single",
        help="处理模式：single 为单报销包；batch-subfolders 为一级子文件夹批量报销包",
    )
    organize.add_argument("--apply", action="store_true", help="复制并重命名文件；默认只生成预览")
    organize.add_argument("--project-name", help="项目名；优先级高于 trip_info.json")
    organize.add_argument("--traveler", help="人员；优先级高于 trip_info.json")
    organize.add_argument("--department", help="部门；优先级高于 trip_info.json")
    organize.add_argument("--trip-start-date", help="出差开始日期 YYYY-MM-DD；优先级高于 trip_info.json")
    organize.add_argument("--trip-end-date", help="出差结束日期 YYYY-MM-DD；优先级高于 trip_info.json")
    organize.add_argument("--daily-meal-allowance", help="单日餐补金额；默认 50，可在 trip_info.json 中配置")
    organize.add_argument("--city-transport-daily-limit", help="市内交通每日标准；默认 100，可在配置文件中配置")
    organize.add_argument("--lodging-daily-limit", help="住宿每晚上限；默认不启用住宿超标判断")
    organize.add_argument("--enable-llm-review", action="store_true", help="启用 OpenAI 兼容大模型行程复核")
    organize.add_argument("--max-workers", type=int, default=3, help="并发识别数量，默认 3")
    organize.add_argument("--timeout-seconds", type=int, default=120, help="单个 OCR 请求超时秒数，默认 120")
    organize.add_argument("--request-timeout-seconds", type=int, default=60, help="单个 HTTP 请求超时秒数（轮询/下载），默认 60")
    organize.add_argument("--retry-attempts", type=int, default=3, help="OCR 失败后重试次数，默认 3")
    organize.add_argument("--retry-delay", type=float, default=1.0, help="重试初始间隔秒数（指数退避），默认 1.0")
    organize.add_argument(
        "--ocr-provider",
        choices=["async_jobs"],
        default="async_jobs",
        help="OCR 调用模式；默认 async_jobs (PaddleOCR-VL-1.6)",
    )
    ui = subparsers.add_parser("ui", help="启动本地 Web UI")
    ui.add_argument("--host", default="127.0.0.1", help="监听地址，默认 127.0.0.1")
    ui.add_argument("--port", type=int, default=8765, help="监听端口，默认 8765")
    return parser


def main(argv: Optional[list[str]] = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "ui":
        run_ui(host=args.host, port=args.port)
        return 0
    try:
        trip_info = _trip_info_from_args(args)
        config = load_agent_config(args.config)
        provider = build_ocr_provider(
            config,
            args.ocr_provider,
            args.timeout_seconds,
            retry_max_attempts=args.retry_attempts,
            retry_base_delay_seconds=args.retry_delay,
            request_timeout_seconds=args.request_timeout_seconds,
        )
        trip_audit_policy = build_trip_audit_policy(args, config)
        if normalize_mode(args.mode) == ORGANIZE_MODE_BATCH_SUBFOLDERS:
            result = organize_batch_subfolders(
                folder=args.folder,
                trip_info=trip_info,
                out_dir=args.out_dir,
                apply=args.apply,
                ocr_provider=provider,
                max_workers=args.max_workers,
                trip_audit_policy=trip_audit_policy,
            )
        else:
            result = organize_folder(
                folder=args.folder,
                trip_info_path=args.trip_info,
                trip_info=trip_info,
                out_dir=args.out_dir,
                apply=args.apply,
                ocr_provider=provider,
                max_workers=args.max_workers,
                trip_audit_policy=trip_audit_policy,
            )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"输出目录: {result.output_dir}")
    if normalize_mode(args.mode) == ORGANIZE_MODE_BATCH_SUBFOLDERS:
        counts = {state: sum(1 for item in result.items if item.state == state) for state in ["done", "failed", "skipped"]}
        print(f"报销包数: {len(result.items)}")
        print(f"完成: {counts['done']}，失败: {counts['failed']}，跳过: {counts['skipped']}")
    else:
        print(f"识别文件数: {len(result.records)}")
        print(f"Excel: {result.output_dir / '00_报销清单.xlsx'}")
    if not args.apply:
        print("当前为预览模式，未复制或重命名原文件。确认清单后可追加 --apply。")
    return 0


def build_ocr_provider(config, provider_name: Optional[str], timeout_seconds: int,
                       retry_max_attempts: Optional[int] = None,
                       retry_base_delay_seconds: Optional[float] = None,
                       request_timeout_seconds: Optional[int] = None):
    return SdkOcrProvider(
        access_token=config.paddleocr_access_token or None,
        timeout_seconds=timeout_seconds,
        request_timeout_seconds=request_timeout_seconds if request_timeout_seconds is not None else config.request_timeout_seconds,
    )


def normalize_mode(value: str) -> str:
    if value == "batch-subfolders":
        return ORGANIZE_MODE_BATCH_SUBFOLDERS
    return ORGANIZE_MODE_SINGLE


def build_trip_audit_policy(args, config) -> TripAuditPolicy:
    return TripAuditPolicy(
        city_transport_daily_limit=args.city_transport_daily_limit or config.city_transport_daily_limit or "100",
        lodging_daily_limit=args.lodging_daily_limit or config.lodging_daily_limit or "",
        enable_llm_review=bool(args.enable_llm_review),
        llm_base_url=config.llm_base_url,
        llm_model=config.llm_model,
        llm_api_key_env=config.llm_api_key_env,
    )


def _trip_info_from_args(args) -> Optional[TripInfo]:
    values = {
        "project_name": args.project_name,
        "traveler": args.traveler,
        "department": args.department,
        "trip_start_date": args.trip_start_date,
        "trip_end_date": args.trip_end_date,
        "daily_meal_allowance": args.daily_meal_allowance,
    }
    if not any(values.values()):
        return None
    optional_keys = {"project_name", "daily_meal_allowance"}
    missing = [key for key, value in values.items() if not value and key not in optional_keys]
    if missing:
        raise ValueError("CLI trip info is incomplete: " + ", ".join(missing))
    return TripInfo(
        project_name=values["project_name"] or args.folder.name,
        traveler=values["traveler"],
        department=values["department"],
        trip_start_date=values["trip_start_date"],
        trip_end_date=values["trip_end_date"],
        daily_meal_allowance=values["daily_meal_allowance"] or "50",
    )


if __name__ == "__main__":
    raise SystemExit(main())
