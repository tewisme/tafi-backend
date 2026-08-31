import sqlite3
import random
import string
import datetime
import os
from rich.console import Console
from rich.table import Table
from rich.prompt import Prompt, Confirm

console = Console()
DB_FILE = "licenses.db"

def clear_screen():
    # Xóa console để đỡ rối
    os.system('cls' if os.name == 'nt' else 'clear')

def pause():
    Prompt.ask("\n[dim]👉 Nhấn Enter để quay lại Menu...[/dim]", default="")

def get_conn():
    return sqlite3.connect(DB_FILE)

def init_db():
    conn = get_conn()
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS users (
            license_key TEXT PRIMARY KEY,
            hwid TEXT,
            tier TEXT DEFAULT 'free',
            api_credits INTEGER DEFAULT 0,
            tool_used_this_month INTEGER DEFAULT 0,
            last_reset_month TEXT,
            expires_at DATETIME
        )''')
    conn.commit()
    conn.close()

def generate_key(prefix="TAFI"):
    chars = string.ascii_uppercase + string.digits
    p1 = ''.join(random.choices(chars, k=5))
    p2 = ''.join(random.choices(chars, k=5))
    return f"{prefix}-{p1}-{p2}"

def list_keys():
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT license_key, hwid, tier, api_credits, tool_used_this_month, expires_at FROM users")
    rows = c.fetchall()
    conn.close()

    if not rows:
        console.print("\n[yellow]Danh sách trống. Chưa có Key nào trong hệ thống.[/yellow]")
        return

    table = Table(title="📋 DANH SÁCH LICENSE KEYS", show_lines=True)
    table.add_column("License Key", style="cyan", no_wrap=True)
    table.add_column("HWID", style="magenta")
    table.add_column("Tier", style="green")
    table.add_column("Credits (Tokens)", justify="right", style="yellow")
    table.add_column("Used (Tháng)", justify="right")
    table.add_column("Ngày Hết Hạn", style="red")

    for r in rows:
        key, hwid, tier, credits, used, expires = r
        hwid_str = hwid if hwid else "[dim]Chưa kích hoạt[/dim]"
        table.add_row(key, hwid_str, tier, f"{credits:,}", str(used), str(expires))
    
    console.print("\n")
    console.print(table)

def create_key():
    console.print("\n[bold cyan]--- ➕ TẠO KEY MỚI ---[/bold cyan]")
    prefix = Prompt.ask("Nhập Tiền tố Key", default="TAFI")
    tier = Prompt.ask("Loại Key (Tier)", choices=["free", "vip", "pro"], default="vip")
    credits = int(Prompt.ask("Số dư Credits ban đầu", default="50000"))
    
    new_key = generate_key(prefix)
    # Expiry 1 year by default
    expires = (datetime.datetime.now() + datetime.timedelta(days=365)).strftime("%Y-%m-%d %H:%M:%S")

    conn = get_conn()
    c = conn.cursor()
    try:
        c.execute("INSERT INTO users (license_key, tier, api_credits, expires_at) VALUES (?, ?, ?, ?)", 
                  (new_key, tier, credits, expires))
        conn.commit()
        console.print(f"\n[bold green]✅ Đã tạo Key thành công![/bold green]")
        console.print(f"👉 [bold yellow]{new_key}[/bold yellow] | Credits: {credits:,} | Hạn: {expires}")
    except sqlite3.IntegrityError:
        console.print("[bold red]Lỗi: Trùng Key.[/bold red]")
    finally:
        conn.close()

def add_credits():
    console.print("\n[bold cyan]--- 💰 NẠP THÊM CREDITS ---[/bold cyan]")
    key = Prompt.ask("Nhập License Key cần nạp")
    amount = int(Prompt.ask("Số Credits nạp thêm", default="10000"))

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT api_credits FROM users WHERE license_key = ?", (key,))
    row = c.fetchone()
    if not row:
        console.print(f"[bold red]❌ Không tìm thấy Key: {key}[/bold red]")
        conn.close()
        return

    c.execute("UPDATE users SET api_credits = api_credits + ? WHERE license_key = ?", (amount, key))
    conn.commit()
    conn.close()
    console.print(f"\n[bold green]✅ Đã nạp {amount:,} Credits vào tài khoản![/bold green]")
    console.print(f"💰 Số dư cũ: {row[0]:,} -> Số dư mới: {row[0] + amount:,}")


def extend_key():
    console.print("\n[bold cyan]--- ⏳ GIA HẠN BẢN QUYỀN ---[/bold cyan]")
    key = Prompt.ask("Nhập License Key cần gia hạn")
    
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT expires_at FROM users WHERE license_key = ?", (key,))
    row = c.fetchone()
    if not row:
        console.print(f"[bold red]❌ Không tìm thấy Key: {key}[/bold red]")
        conn.close()
        return

    months_str = Prompt.ask("Số tháng muốn gia hạn thêm", default="1")
    try:
        months = int(months_str)
        if months <= 0: raise ValueError
    except:
        console.print("[bold red]❌ Số tháng không hợp lệ![/bold red]")
        conn.close()
        return

    current_expiry_str = row[0]
    try:
        current_expiry = datetime.datetime.strptime(current_expiry_str, "%Y-%m-%d %H:%M:%S")
        if current_expiry < datetime.datetime.now():
            current_expiry = datetime.datetime.now()
    except:
        current_expiry = datetime.datetime.now()
        
    new_expiry = current_expiry + datetime.timedelta(days=months * 30)
    new_expiry_str = new_expiry.strftime("%Y-%m-%d %H:%M:%S")

    c.execute("UPDATE users SET expires_at = ? WHERE license_key = ?", (new_expiry_str, key))
    conn.commit()
    conn.close()
    
    console.print(f"\n[bold green]✅ Đã gia hạn thành công {months} tháng![/bold green]")
    console.print(f"⏳ Hạn cũ: {current_expiry_str} -> [bold yellow]Hạn mới: {new_expiry_str}[/bold yellow]")


def reset_hwid():
    console.print("\n[bold cyan]--- 🔓 RESET HWID ---[/bold cyan]")
    key = Prompt.ask("Nhập License Key cần Reset")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT hwid FROM users WHERE license_key = ?", (key,))
    row = c.fetchone()
    if not row:
        console.print(f"[bold red]❌ Không tìm thấy Key: {key}[/bold red]")
        conn.close()
        return

    if Confirm.ask(f"Xác nhận Reset HWID cho Key {key}?", default=True):
        c.execute("UPDATE users SET hwid = NULL WHERE license_key = ?", (key,))
        conn.commit()
        console.print(f"\n[bold green]✅ Đã giải phóng HWID thành công! Khách có thể đăng nhập trên máy mới.[/bold green]")
    conn.close()

def delete_key():
    console.print("\n[bold cyan]--- ❌ XÓA KEY ---[/bold cyan]")
    key = Prompt.ask("Nhập License Key cần Xóa")

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM users WHERE license_key = ?", (key,))
    if not c.fetchone():
        console.print(f"[bold red]❌ Không tìm thấy Key: {key}[/bold red]")
        conn.close()
        return
    
    if Confirm.ask(f"⚠️ [bold red]CẢNH BÁO: Bạn có chắc chắn muốn xóa vĩnh viễn Key {key}?[/bold red]"):
        c.execute("DELETE FROM users WHERE license_key = ?", (key,))
        conn.commit()
        console.print(f"\n[bold green]✅ Đã xóa Key thành công![/bold green]")
    conn.close()

def main_menu():
    init_db()
    while True:
        clear_screen()
        console.print("\n" + "="*50)
        console.print("[bold magenta]👑 TAFI VIDEO TOOL - ADMIN CLI 👑[/bold magenta]", justify="center")
        console.print("="*50)
        console.print("1. [cyan]📋 Danh sách Users / Keys[/cyan]")
        console.print("2. [green]➕ Tạo License Key mới[/green]")
        console.print("3. [yellow]💰 Nạp Token / Credit[/yellow]")
        console.print("4. [magenta]⏳ Gia hạn Bản quyền (Thêm tháng)[/magenta]")
        console.print("5. [blue]🔓 Reset HWID (Đổi máy)[/blue]")
        console.print("6. [red]❌ Xóa License Key[/red]")
        console.print("0. [dim]Thoát[/dim]")
        
        choice = Prompt.ask("\n👉 [bold]Chọn chức năng[/bold]", choices=["1", "2", "3", "4", "5", "6", "0"])

        clear_screen()
        
        if choice == "1":
            list_keys()
            pause()
        elif choice == "2":
            create_key()
            pause()
        elif choice == "3":
            add_credits()
            pause()
        elif choice == "4":
            extend_key()
            pause()
        elif choice == "5":
            reset_hwid()
            pause()
        elif choice == "6":
            delete_key()
            pause()
        elif choice == "0":
            console.print("[dim]👋 Tạm biệt Sếp![/dim]")
            break

if __name__ == "__main__":
    try:
        main_menu()
    except KeyboardInterrupt:
        console.print("\n[dim]Đã hủy thao tác.[/dim]")
