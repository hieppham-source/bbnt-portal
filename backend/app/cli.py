"""
CLI vận hành / chạy tay không cần web (Render › Shell, hoặc máy local).

    python -m app.cli check                          # cấu hình, Sheet master (dòng lỗi), template
    python -m app.cli generate --entity EZS --channel FB --brand ORGANICA --period 05.26 --campaign BDAY \\
        --order-url "https://docs.google.com/spreadsheets/d/.../edit#gid=0" --out ./out
    # Không cần Google: Sheet master + file order tải về dạng .xlsx
    python -m app.cli generate --master-file deploy/MASTER_BBNT_TEMPLATE.xlsx --order-file order.xlsx ...

Đọc Google Sheet từ dòng lệnh: dùng access token OAuth2 của chính bạn:
``--access-token`` / biến ``GOOGLE_ACCESS_TOKEN`` (lấy ở developers.google.com/oauthplayground, scope
``https://www.googleapis.com/auth/spreadsheets.readonly``; token sống 1 giờ).
"""
from __future__ import annotations

import argparse
import os
import sys
from decimal import Decimal
from pathlib import Path

from app.config import DEFAULT_TEMPLATES_DIR, ConfigError, get_settings
from app.errors import BbntError
from app.master.models import MasterData
from app.master.repository import build_master, google_sheet_source, xlsx_source
from app.services.generator import BbntGenerator, BbntInput, OrderOpener
from app.services.sheets import GoogleSheetsReader, SheetAccessError, parse_sheet_url, xlsx_workbook
from app.services.templates import TemplateRegistry


def _ok(msg: str) -> None:
    print(f"  [OK]   {msg}")


def _fail(msg: str) -> None:
    print(f"  [FAIL] {msg}")


def _warn(msg: str) -> None:
    print(f"  [WARN] {msg}")


def _google_reader() -> GoogleSheetsReader:
    token = (os.environ.get("GOOGLE_ACCESS_TOKEN") or "").strip()
    if not token:
        raise ConfigError("Cần --access-token (hoặc biến GOOGLE_ACCESS_TOKEN) để đọc Google Sheet")
    return GoogleSheetsReader.from_access_token(token)


def _load_master(master_file: str | None) -> MasterData:
    if master_file:
        return build_master(xlsx_source(master_file)())
    s = get_settings()
    if s.master_sheet_url:
        return build_master(google_sheet_source(_google_reader(), parse_sheet_url(s.master_sheet_url))())
    if s.master_file:
        return build_master(xlsx_source(s.master_file)())
    raise ConfigError("Chưa có MASTER_SHEET_URL (hoặc --master-file)")


def _print_issues(m: MasterData) -> None:
    for i in m.issues:
        (_fail if i.severity == "ERROR" else _warn)(f"{i.tab} dòng {i.row}: {i.message}")


def cmd_check(args: argparse.Namespace) -> int:
    print("Kiểm tra BBNT tool")
    failures = 0
    try:
        s = get_settings()
        _ok(f"Cấu hình hợp lệ (APP_ENV={s.app_env})")
    except ConfigError as exc:
        _fail(str(exc))
        if not args.master_file:
            return 1
        failures += 1
        s = None
    if s is not None:
        _ok("Đọc Google Sheet bằng OAuth2 access token của người dùng (không dùng service account)")
        (_ok if s.google_oauth_client_ids else _warn)(f"OAuth client ID: {len(s.google_oauth_client_ids)} giá trị")
    try:
        m = _load_master(args.master_file)
        _ok(f"Sheet master: {len(m.entities)} pháp nhân, {len(m.brands)} brand, {len(m.contracts)} hợp đồng, "
            f"{len(m.mappings)} mapping")
        _print_issues(m)
        failures += len(m.errors)
    except (ConfigError, SheetAccessError, BbntError) as exc:
        _fail(f"Sheet master: {getattr(exc, 'message', exc)}")
        failures += 1
    reg = TemplateRegistry(s.templates_dir if s else DEFAULT_TEMPLATES_DIR)
    for name, status in reg.check().items():
        (_ok if status == "ok" else _fail)(f"Template {name}: {status}")
        failures += 0 if status == "ok" else 1
    print("KẾT QUẢ:", "SẴN SÀNG" if failures == 0 else f"{failures} lỗi cần xử lý")
    return 0 if failures == 0 else 1


def _order_opener(args: argparse.Namespace) -> OrderOpener:
    if args.order_file:
        path = args.order_file
        return lambda: [xlsx_workbook(Path(path).read_bytes())]
    ref = parse_sheet_url(args.order_url)
    reader = _google_reader()
    return lambda: [reader.open(ref)]


def cmd_generate(args: argparse.Namespace) -> int:
    if bool(args.order_file) == bool(args.order_url):
        print("Cần đúng 1 trong --order-file hoặc --order-url", file=sys.stderr)
        return 2
    try:
        master = _load_master(args.master_file)
        templates = TemplateRegistry(args.templates_dir or (DEFAULT_TEMPLATES_DIR if args.master_file
                                                            else get_settings().templates_dir))
        inp = BbntInput(channel=args.channel, entity=args.entity, period_months=args.period,
                        campaign_name=args.campaign, brand_codes=args.brand, group_code=args.group,
                        contract_no=args.contract, mapping_code=args.mapping,
                        collected_on_behalf=Decimal(args.collected_on_behalf),
                        consulting_fee=Decimal(args.consulting_fee), strip_comments=args.strip_comments,
                        order_tab=args.order_tab)
        r = BbntGenerator(templates).run(master, inp, _order_opener(args), render=not args.dry_run)
    except BbntError as exc:
        print(f"LỖI {exc.code}: {exc.message}", file=sys.stderr)
        if isinstance(exc.details, list):
            for i in exc.details[:30]:
                print(f"   dòng {getattr(i, 'row_number', '?')}: {getattr(i, 'message', i)}", file=sys.stderr)
        elif exc.details:
            print(f"   {exc.details}", file=sys.stderr)
        return 1
    except (ConfigError, SheetAccessError) as exc:
        print(f"LỖI: {exc}", file=sys.stderr)
        return 1
    c = r.commission
    print(f"Số BBNT       : {r.number}")
    print(f"Hợp đồng      : {r.contract.contract_no} (HOP_DONG dòng {r.contract.row}) — hoa hồng {c.commission_rate}")
    if r.order:
        tabs = ", ".join(r.order.candidates)
        print(f"Tab order     : {r.order.tab} (header dòng {r.parse.header_row}; tab khớp: {tabs})")
    print(f"GMV ước tính  : {r.gmv_vnd:,}")
    print(f"NMV (Đã giao) : {r.nmv_vnd:,}")
    print(f"Hoa hồng      : {c.revenue:,}  | {r.tax.label}: VAT {c.vat_amount:,} / khấu trừ {c.tax_deduction_amount:,}")
    print(f"Tổng thanh toán: {c.total_payable:,}")
    print(f"Cảnh báo      : {len(r.warnings)} dòng")
    if r.content is not None:
        from app.api.v1.bbnt import download_filename

        out = Path(args.out)
        out.mkdir(parents=True, exist_ok=True)
        path = out / download_filename(str(r.number))
        path.write_bytes(r.content)
        print(f"Đã ghi        : {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m app.cli")
    sub = p.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("check", help="Kiểm tra cấu hình, Sheet master, template")
    c.add_argument("--master-file", help="Dùng file .xlsx thay cho Sheet master")
    c.add_argument("--access-token", help="Access token OAuth Google của bạn (đọc Sheet master)")
    c.set_defaults(fn=cmd_check)

    g = sub.add_parser("generate", help="Tạo 1 BBNT ra file .docx")
    g.add_argument("--entity", required=True)
    g.add_argument("--channel", required=True)
    scope = g.add_mutually_exclusive_group(required=True)
    scope.add_argument("--brand", action="append", help="Lặp lại để gộp nhiều brand cùng group")
    scope.add_argument("--group")
    g.add_argument("--period", action="append", required=True, help="05.26 — lặp lại cho nhiều tháng")
    g.add_argument("--campaign", required=True)
    g.add_argument("--contract", help="Số hợp đồng (khi có nhiều hợp đồng cùng hiệu lực)")
    g.add_argument("--mapping")
    g.add_argument("--order-url")
    g.add_argument("--order-file")
    g.add_argument("--order-tab", help="Tên tab trong file order (bỏ trống để tự chọn)")
    g.add_argument("--master-file")
    g.add_argument("--access-token", help="Access token OAuth Google của bạn (đọc Sheet master + file order)")
    g.add_argument("--templates-dir")
    g.add_argument("--collected-on-behalf", default="0")
    g.add_argument("--consulting-fee", default="0")
    g.add_argument("--strip-comments", action="store_true")
    g.add_argument("--dry-run", action="store_true", help="Chỉ tính, không tạo file")
    g.add_argument("--out", default=".")
    g.set_defaults(fn=cmd_generate)
    args = p.parse_args(argv)
    if args.access_token:
        os.environ["GOOGLE_ACCESS_TOKEN"] = args.access_token
    result: int = args.fn(args)
    return result


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
