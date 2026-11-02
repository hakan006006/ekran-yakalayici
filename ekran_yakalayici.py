#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Kayıpsız & Orijinal Çözünürlüklü Ekran Görüntüsü Yakalama Aracı
----------------------------------------------------------------
Özellikler:
- %100 Kayıpsız PNG Kaydı: Sıkıştırma kaybı (JPEG artefaktı) olmadan ham piksel kalitesinde kayıt.
- Gerçek Orijinal Çözünürlük (High-DPI Aware): Windows/macOS/Linux ölçeklendirme bulanıklığını
  devre dışı bırakarak ekran kartı tamponundaki (framebuffer) birebir pikselleri yakalar.
- Donmuş Ekran Üzerinde Bölgesel Yakalama: Kısayola bastığınız an ekran dondurulur; fareyle
  seçtiğiniz alan yeniden boyutlandırılmadan birebir piksel çözünürlüğünde kesilir.
- Tek Tuşla Tam Ekran Yakalama: Print Screen tuşuyla veya arayüzden anında tam ekran yakalama.
- Otomatik Kaydetme: Belirlediğiniz klasöre tarih, saat ve çözünürlük etiketiyle otomatik kaydeder.
- Doğrudan Panoya Kopyalama: Görüntüyü Word, LaTeX editörleri (Overleaf/TeXstudio) veya diğer
  programlara doğrudan Ctrl+V ile yapıştırabilmeniz için panoya kopyalar.
- Özelleştirilebilir Global Kısayollar: Arka planda çalışırken kısayol tuşlarını dinler.

Gerekli Kütüphaneler (Kurulum):
    pip install pillow mss keyboard pywin32
"""

import ctypes
import datetime
import io
import json
import os
import platform
import subprocess
import sys
import threading
import time
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

# ---------------------------------------------------------------------------
# 1. YÜKSEK ÇÖZÜNÜRLÜK (HIGH-DPI) FARKINDALIĞI - YENİDEN BOYUTLANDIRMAYI ÖNLER
# ---------------------------------------------------------------------------
def enable_native_dpi_awareness() -> None:
    """
    İşletim sisteminin (özellikle Windows %125, %150, %200 ölçeklendirme)
    uygulamayı sanal düşük çözünürlükte çalıştırıp görüntüyü bulanıklaştırmasını engeller.
    Donanımın gerçek piksel çözünürlüğünü (1:1) zorunlu kılar.
    """
    if platform.system() == "Windows":
        try:
            # Windows 8.1 / 10 / 11 Per-Monitor DPI Aware v2
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                # Windows Vista / 7 / 8 DPI Aware yedek çağrısı
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


enable_native_dpi_awareness()

# Üçüncü parti görüntü ve kısayol kütüphaneleri
try:
    from PIL import Image, ImageEnhance, ImageGrab, ImageTk
except ImportError:
    print("Hata: 'Pillow' kütüphanesi bulunamadı. Lütfen 'pip install pillow' komutunu çalıştırın.")
    sys.exit(1)

try:
    import mss
    HAS_MSS = True
except ImportError:
    HAS_MSS = False

try:
    import keyboard
    HAS_KEYBOARD = True
except ImportError:
    HAS_KEYBOARD = False


# ---------------------------------------------------------------------------
# 2. AYAR YÖNETİCİSİ
# ---------------------------------------------------------------------------
CONFIG_FILE = Path.home() / ".lossless_screenshot_config.json"

DEFAULT_CONFIG = {
    "save_directory": str(Path.home() / "Pictures" / "Screenshots"),
    "auto_save": True,
    "copy_to_clipboard": True,
    "copy_latex_code": False,
    "include_all_monitors": True,
    "hotkey_fullscreen": "print screen",
    "hotkey_region": "ctrl+shift+s",
    "filename_prefix": "EkranGoruntusu",
}


class ConfigManager:
    """Kullanıcı tercihlerini JSON dosyasında saklar ve yükler."""

    def __init__(self, config_path: Path = CONFIG_FILE) -> None:
        self.config_path = config_path
        self.data = DEFAULT_CONFIG.copy()
        self.load()

    def load(self) -> None:
        if self.config_path.exists():
            try:
                with open(self.config_path, "r", encoding="utf-8") as f:
                    loaded = json.load(f)
                    self.data.update(loaded)
            except Exception:
                pass
        os.makedirs(self.data["save_directory"], exist_ok=True)

    def save(self) -> None:
        try:
            os.makedirs(self.data["save_directory"], exist_ok=True)
            with open(self.config_path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, ensure_ascii=False, indent=2)
        except Exception as exc:
            print(f"Ayarlar kaydedilemedi: {exc}")


# ---------------------------------------------------------------------------
# 3. KAYIPSIZ EKRAN YAKALAMA VE PANO (CLIPBOARD) MOTORU
# ---------------------------------------------------------------------------
class ScreenCaptureEngine:
    """
    Ekranı hiçbir yeniden örnekleme (resampling) veya sıkıştırma kaybı olmadan
    doğrudan ham pikselleriyle yakalar, kayıpsız PNG olarak kaydeder ve panoya aktarır.
    """

    @staticmethod
    def grab_raw_screen(all_monitors: bool = True) -> tuple[Image.Image, tuple[int, int, int, int]]:
        """
        Tam ekranı orijinal donanım çözünürlüğünde yakalar.
        Dönüş: (PIL.Image (RGB), (left, top, width, height))
        """
        if HAS_MSS:
            with mss.mss() as sct:
                # 0: Tüm monitörlerin birleşimi, 1: Birincil monitör
                monitor_idx = 0 if (all_monitors and len(sct.monitors) > 1) else 1
                mon = sct.monitors[monitor_idx]
                raw = sct.grab(mon)
                # BGRA tamponundan doğrudan 24-bit RGB görüntü oluştur (yeniden boyutlandırma yok)
                img = Image.frombytes("RGB", raw.size, raw.bgra, "raw", "BGRX")
                bounds = (mon["left"], mon["top"], mon["width"], mon["height"])
                return img, bounds

        # Yedek: PIL.ImageGrab
        img = ImageGrab.grab(all_screens=all_monitors)
        if img.mode != "RGB":
            img = img.convert("RGB")
        return img, (0, 0, img.width, img.height)

    @staticmethod
    def save_lossless_png(image: Image.Image, save_dir: str, prefix: str = "EkranGoruntusu") -> Path:
        """
        Görüntüyü %100 kayıpsız PNG formatında, orijinal piksel boyutlarını koruyarak kaydeder.
        """
        os.makedirs(save_dir, exist_ok=True)
        timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S_%f")[:-3]
        width, height = image.size
        filename = f"{prefix}_{timestamp}_{width}x{height}.png"
        filepath = Path(save_dir) / filename

        # compress_level=1: Zlib kayıpsız sıkıştırmada maksimum hız + %100 kayıpsız piksel verisi
        # Görüntü pikselleri kesinlikle değiştirilmez veya yeniden boyutlandırılmaz.
        image.save(
            filepath,
            format="PNG",
            optimize=False,
            compress_level=1,
        )
        return filepath

    @staticmethod
    def copy_image_to_clipboard(image: Image.Image) -> bool:
        """
        Görüntüyü işletim sistemi panosuna (Clipboard) kayıpsız olarak kopyalar.
        Word, PowerPoint, LaTeX editörleri ve görüntü işleme yazılımlarına doğrudan Ctrl+V ile yapıştırılabilir.
        """
        system = platform.system()

        if system == "Windows":
            # 1. Yöntem: pywin32 (CF_DIB - sıkıştırmasız BMP/DIB verisi, Word ile %100 uyumlu)
            try:
                import win32clipboard

                with io.BytesIO() as output:
                    image.convert("RGB").save(output, "BMP")
                    dib_data = output.getvalue()[14:]  # 14 baytlık BMP dosya başlığını çıkar

                win32clipboard.OpenClipboard()
                try:
                    win32clipboard.EmptyClipboard()
                    win32clipboard.SetClipboardData(win32clipboard.CF_DIB, dib_data)
                finally:
                    win32clipboard.CloseClipboard()
                return True
            except Exception:
                # 2. Yöntem: PowerShell System.Windows.Forms.Clipboard yedeği
                try:
                    temp_png = Path(os.environ.get("TEMP", ".")) / "_clipboard_lossless_temp.png"
                    image.save(temp_png, format="PNG", compress_level=1)
                    ps_cmd = (
                        "Add-Type -AssemblyName System.Windows.Forms; "
                        "Add-Type -AssemblyName System.Drawing; "
                        f"$img = [System.Drawing.Image]::FromFile('{str(temp_png)}'); "
                        "[System.Windows.Forms.Clipboard]::SetImage($img); "
                        "$img.Dispose();"
                    )
                    subprocess.run(
                        ["powershell", "-NoProfile", "-Command", ps_cmd],
                        check=True,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    return True
                except Exception as exc:
                    print(f"Windows pano hatası: {exc}")
                    return False

        elif system == "Darwin":  # macOS
            try:
                temp_png = Path("/tmp/_clipboard_lossless_temp.png")
                image.save(temp_png, format="PNG", compress_level=1)
                applescript = (
                    f'set the clipboard to (read (POSIX file "{temp_png}") as «class PNGf»)'
                )
                subprocess.run(["osascript", "-e", applescript], check=True)
                return True
            except Exception as exc:
                print(f"macOS pano hatası: {exc}")
                return False

        else:  # Linux (X11 / Wayland)
            try:
                with io.BytesIO() as output:
                    image.save(output, format="PNG", compress_level=1)
                    png_bytes = output.getvalue()

                # Wayland (wl-copy) veya X11 (xclip) kontrolü
                for cmd in (
                    ["xclip", "-selection", "clipboard", "-t", "image/png", "-i"],
                    ["wl-copy", "--type", "image/png"],
                ):
                    try:
                        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE)
                        proc.communicate(input=png_bytes, timeout=3)
                        if proc.returncode == 0:
                            return True
                    except FileNotFoundError:
                        continue
            except Exception as exc:
                print(f"Linux pano hatası: {exc}")
            return False


# ---------------------------------------------------------------------------
# 4. BÖLGESEL EKRAN SEÇİM KATMANI (SNIPPING OVERLAY)
# ---------------------------------------------------------------------------
class RegionSelectorOverlay:
    """
    Kısayola basıldığı an yakalanmış tam çözünürlüklü ekran görüntüsü üzerinde
    kullanıcının fareyle piksel hassasiyetinde alan seçmesini sağlar.
    Seçim çerçevesi görüntünün içine asla karışmaz, çünkü kesim işlemi
    önceden yakalanan saf (orijinal) görüntü üzerinden yapılır.
    """

    def __init__(
        self,
        parent: tk.Tk,
        frozen_image: Image.Image,
        bounds: tuple[int, int, int, int],
        on_region_selected,
    ) -> None:
        self.parent = parent
        self.frozen_image = frozen_image
        self.left, self.top, self.width, self.height = bounds
        self.on_region_selected = on_region_selected

        self.start_x: int | None = None
        self.start_y: int | None = None
        self.rect_id: int | None = None
        self.dim_crop_id: int | None = None
        self.label_bg_id: int | None = None
        self.label_text_id: int | None = None
        self.bright_crop_tk: ImageTk.PhotoImage | None = None

        # Üst katman penceresi oluştur
        self.win = tk.Toplevel(parent)
        self.win.withdraw()
        self.win.overrideredirect(True)
        self.win.attributes("-topmost", True)
        self.win.geometry(f"{self.width}x{self.height}+{self.left}+{self.top}")
        self.win.configure(cursor="crosshair")

        # Arka planı hafif karartılmış görüntü olarak hazırla
        enhancer = ImageEnhance.Brightness(self.frozen_image)
        dimmed_img = enhancer.enhance(0.45)
        self.dimmed_tk = ImageTk.PhotoImage(dimmed_img)

        self.canvas = tk.Canvas(
            self.win,
            width=self.width,
            height=self.height,
            highlightthickness=0,
            bd=0,
            cursor="crosshair",
        )
        self.canvas.pack(fill=tk.BOTH, expand=True)
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self.dimmed_tk)

        # Bilgi başlığı
        self.help_banner = self.canvas.create_rectangle(
            self.width // 2 - 260,
            18,
            self.width // 2 + 260,
            54,
            fill="#0F172A",
            outline="#38BDF8",
            width=1,
        )
        self.help_text = self.canvas.create_text(
            self.width // 2,
            36,
            text="Fareyle alanı seçin ve bırakın  •  İptal için ESC veya Sağ Tık",
            fill="#F8FAFC",
            font=("Segoe UI", 11, "bold"),
        )

        # Fare ve klavye olayları
        self.canvas.bind("<ButtonPress-1>", self.on_mouse_down)
        self.canvas.bind("<B1-Motion>", self.on_mouse_drag)
        self.canvas.bind("<ButtonRelease-1>", self.on_mouse_up)
        self.canvas.bind("<ButtonPress-3>", lambda e: self.cancel())
        self.win.bind("<Escape>", lambda e: self.cancel())

        self.win.deiconify()
        self.win.focus_force()
        self.win.grab_set()

    def on_mouse_down(self, event: tk.Event) -> None:
        self.start_x = self.canvas.canvasx(event.x)
        self.start_y = self.canvas.canvasy(event.y)

    def on_mouse_drag(self, event: tk.Event) -> None:
        if self.start_x is None or self.start_y is None:
            return

        cur_x = max(0, min(self.width, int(self.canvas.canvasx(event.x))))
        cur_y = max(0, min(self.height, int(self.canvas.canvasy(event.y))))

        x1 = int(min(self.start_x, cur_x))
        y1 = int(min(self.start_y, cur_y))
        x2 = int(max(self.start_x, cur_x))
        y2 = int(max(self.start_y, cur_y))

        # Önceki çizimleri güncelle
        if self.dim_crop_id:
            self.canvas.delete(self.dim_crop_id)
        if self.rect_id:
            self.canvas.delete(self.rect_id)
        if self.label_bg_id:
            self.canvas.delete(self.label_bg_id)
        if self.label_text_id:
            self.canvas.delete(self.label_text_id)

        if x2 - x1 > 2 and y2 - y1 > 2:
            # Seçili bölgeyi %100 orijinal parlaklığında göster
            bright_region = self.frozen_image.crop((x1, y1, x2, y2))
            self.bright_crop_tk = ImageTk.PhotoImage(bright_region)
            self.dim_crop_id = self.canvas.create_image(
                x1, y1, anchor=tk.NW, image=self.bright_crop_tk
            )

        # Seçim çerçevesi
        self.rect_id = self.canvas.create_rectangle(
            x1, y1, x2, y2, outline="#38BDF8", width=2
        )

        # Canlı piksel çözünürlük etiketi
        w_px = x2 - x1
        h_px = y2 - y1
        badge_text = f"{w_px} × {h_px} px (Orijinal PNG)"
        label_y = y1 - 28 if y1 > 34 else y2 + 8

        self.label_bg_id = self.canvas.create_rectangle(
            x1, label_y, x1 + 195, label_y + 24, fill="#0F172A", outline="#38BDF8"
        )
        self.label_text_id = self.canvas.create_text(
            x1 + 97,
            label_y + 12,
            text=badge_text,
            fill="#38BDF8",
            font=("Consolas", 10, "bold"),
        )

    def on_mouse_up(self, event: tk.Event) -> None:
        if self.start_x is None or self.start_y is None:
            return

        end_x = max(0, min(self.width, int(self.canvas.canvasx(event.x))))
        end_y = max(0, min(self.height, int(self.canvas.canvasy(event.y))))

        x1 = int(min(self.start_x, end_x))
        y1 = int(min(self.start_y, end_y))
        x2 = int(max(self.start_x, end_x))
        y2 = int(max(self.start_y, end_y))

        self.close()

        # Çok küçük yanlış tıklamaları yoksay (en az 5x5 piksel)
        if (x2 - x1) >= 5 and (y2 - y1) >= 5:
            # Saf (çizimsiz) orijinal görüntüden birebir piksel kesimi
            cropped = self.frozen_image.crop((x1, y1, x2, y2))
            self.on_region_selected(cropped)
        else:
            self.on_region_selected(None)

    def cancel(self) -> None:
        self.close()
        self.on_region_selected(None)

    def close(self) -> None:
        try:
            self.win.grab_release()
            self.win.destroy()
        except Exception:
            pass


# ---------------------------------------------------------------------------
# 5. ANA MASAÜSTÜ UYGULAMASI ARAYÜZÜ
# ---------------------------------------------------------------------------
class LosslessScreenshotApp:
    """Kayıpsız Ekran Görüntüsü Yakalama Aracı Ana Penceresi."""

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Kayıpsız PNG Ekran Yakalama Aracı (1:1 Orijinal Çözünürlük)")
        self.root.geometry("680x560")
        self.root.minsize(640, 520)
        self.root.configure(bg="#0F172A")

        self.config_mgr = ConfigManager()
        self.is_capturing = False
        self.last_saved_path: Path | None = None
        self.preview_photo: ImageTk.PhotoImage | None = None

        self._setup_styles()
        self._build_ui()
        self._register_global_hotkeys()

        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

    def _setup_styles(self) -> None:
        style = ttk.Style(self.root)
        style.theme_use("clam")

        style.configure("Main.TFrame", background="#0F172A")
        style.configure("Card.TFrame", background="#1E293B", relief="flat")
        style.configure(
            "Title.TLabel",
            background="#0F172A",
            foreground="#F8FAFC",
            font=("Segoe UI", 15, "bold"),
        )
        style.configure(
            "Subtitle.TLabel",
            background="#0F172A",
            foreground="#94A3B8",
            font=("Segoe UI", 9),
        )
        style.configure(
            "Section.TLabel",
            background="#1E293B",
            foreground="#38BDF8",
            font=("Segoe UI", 10, "bold"),
        )
        style.configure(
            "Field.TLabel",
            background="#1E293B",
            foreground="#E2E8F0",
            font=("Segoe UI", 10),
        )
        style.configure(
            "Status.TLabel",
            background="#0F172A",
            foreground="#4ADE80",
            font=("Consolas", 10, "bold"),
        )
        style.configure(
            "Primary.TButton",
            background="#0284C7",
            foreground="#FFFFFF",
            font=("Segoe UI", 10, "bold"),
            padding=(14, 10),
        )
        style.map("Primary.TButton", background=[("active", "#0369A1")])

        style.configure(
            "Secondary.TButton",
            background="#334155",
            foreground="#F8FAFC",
            font=("Segoe UI", 9, "bold"),
            padding=(10, 7),
        )
        style.map("Secondary.TButton", background=[("active", "#475569")])

        style.configure(
            "Card.TCheckbutton",
            background="#1E293B",
            foreground="#E2E8F0",
            font=("Segoe UI", 10),
        )
        style.map(
            "Card.TCheckbutton",
            background=[("active", "#1E293B")],
            foreground=[("active", "#FFFFFF")],
        )

    def _build_ui(self) -> None:
        main = ttk.Frame(self.root, style="Main.TFrame", padding=18)
        main.pack(fill=tk.BOTH, expand=True)

        # Üst Başlık
        header = ttk.Frame(main, style="Main.TFrame")
        header.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(
            header,
            text="Kayıpsız PNG Ekran Görüntüsü Yakalayıcı",
            style="Title.TLabel",
        ).pack(anchor=tk.W)
        ttk.Label(
            header,
            text="Sıfır JPEG Sıkıştırması  •  1:1 Orijinal Donanım Çözünürlüğü  •  Word & LaTeX Pano Desteği",
            style="Subtitle.TLabel",
        ).pack(anchor=tk.W, pady=(2, 0))

        # Hızlı Yakalama Butonları
        action_card = ttk.Frame(main, style="Card.TFrame", padding=14)
        action_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(action_card, text="HIZLI YAKALAMA İŞLEMLERİ", style="Section.TLabel").pack(
            anchor=tk.W, pady=(0, 8)
        )

        btn_row = ttk.Frame(action_card, style="Card.TFrame")
        btn_row.pack(fill=tk.X)

        self.btn_region = ttk.Button(
            btn_row,
            text=f"✂ Bölgesel Ekran Yakala ({self.config_mgr.data['hotkey_region'].upper()})",
            style="Primary.TButton",
            command=self.trigger_region_capture_from_ui,
        )
        self.btn_region.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        self.btn_fullscreen = ttk.Button(
            btn_row,
            text=f"🖥 Tam Ekran Yakala ({self.config_mgr.data['hotkey_fullscreen'].upper()})",
            style="Primary.TButton",
            command=self.trigger_fullscreen_capture_from_ui,
        )
        self.btn_fullscreen.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(6, 0))

        # Kayıt Klasörü ve Seçenekler Kartı
        settings_card = ttk.Frame(main, style="Card.TFrame", padding=14)
        settings_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(settings_card, text="OTOMATİK KAYIT & PANO AYARLARI", style="Section.TLabel").pack(
            anchor=tk.W, pady=(0, 8)
        )

        dir_row = ttk.Frame(settings_card, style="Card.TFrame")
        dir_row.pack(fill=tk.X, pady=(0, 10))

        ttk.Label(dir_row, text="Kayıt Klasörü:", style="Field.TLabel").pack(
            side=tk.LEFT, padx=(0, 8)
        )

        self.dir_var = tk.StringVar(value=self.config_mgr.data["save_directory"])
        dir_entry = ttk.Entry(dir_row, textvariable=self.dir_var, font=("Consolas", 9))
        dir_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(
            dir_row,
            text="Klasör Seç...",
            style="Secondary.TButton",
            command=self.choose_directory,
        ).pack(side=tk.LEFT, padx=(0, 6))

        ttk.Button(
            dir_row,
            text="Klasörü Aç",
            style="Secondary.TButton",
            command=self.open_save_directory,
        ).pack(side=tk.LEFT)

        # Onay Kutuları
        self.auto_save_var = tk.BooleanVar(value=self.config_mgr.data["auto_save"])
        self.clipboard_var = tk.BooleanVar(value=self.config_mgr.data["copy_to_clipboard"])
        self.latex_var = tk.BooleanVar(value=self.config_mgr.data["copy_latex_code"])
        self.all_monitors_var = tk.BooleanVar(value=self.config_mgr.data["include_all_monitors"])

        ttk.Checkbutton(
            settings_card,
            text="Görüntüleri belirlenen klasöre otomatik olarak kayıpsız PNG (.png) formatında kaydet",
            variable=self.auto_save_var,
            style="Card.TCheckbutton",
            command=self._sync_preferences,
        ).pack(anchor=tk.W, pady=2)

        ttk.Checkbutton(
            settings_card,
            text="Görüntüyü doğrudan panoya kopyala (Word, LaTeX editörü veya diğer programlara Ctrl+V ile yapıştır)",
            variable=self.clipboard_var,
            style="Card.TCheckbutton",
            command=self._sync_preferences,
        ).pack(anchor=tk.W, pady=2)

        ttk.Checkbutton(
            settings_card,
            text="Çoklu monitör kullanılıyorsa tüm ekranları dahil et",
            variable=self.all_monitors_var,
            style="Card.TCheckbutton",
            command=self._sync_preferences,
        ).pack(anchor=tk.W, pady=2)

        # Kısayol Ayarları Kartı
        hotkey_card = ttk.Frame(main, style="Card.TFrame", padding=14)
        hotkey_card.pack(fill=tk.X, pady=(0, 12))

        ttk.Label(hotkey_card, text="KISAYOL TUŞLARI (ARKA PLANDA ÇALIŞIR)", style="Section.TLabel").pack(
            anchor=tk.W, pady=(0, 8)
        )

        hk_row = ttk.Frame(hotkey_card, style="Card.TFrame")
        hk_row.pack(fill=tk.X)

        ttk.Label(hk_row, text="Tam Ekran Kısayolu:", style="Field.TLabel").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.hk_full_var = tk.StringVar(value=self.config_mgr.data["hotkey_fullscreen"])
        ttk.Entry(hk_row, textvariable=self.hk_full_var, width=16, font=("Consolas", 9)).pack(
            side=tk.LEFT, padx=(0, 16)
        )

        ttk.Label(hk_row, text="Bölgesel Yakalama Kısayolu:", style="Field.TLabel").pack(
            side=tk.LEFT, padx=(0, 6)
        )
        self.hk_region_var = tk.StringVar(value=self.config_mgr.data["hotkey_region"])
        ttk.Entry(hk_row, textvariable=self.hk_region_var, width=16, font=("Consolas", 9)).pack(
            side=tk.LEFT, padx=(0, 12)
        )

        ttk.Button(
            hk_row,
            text="Kısayolları Güncelle",
            style="Secondary.TButton",
            command=self.update_hotkeys,
        ).pack(side=tk.LEFT)

        # Alt Bilgi ve LaTeX Kopyalama Satırı
        bottom_card = ttk.Frame(main, style="Card.TFrame", padding=12)
        bottom_card.pack(fill=tk.BOTH, expand=True)

        bottom_top = ttk.Frame(bottom_card, style="Card.TFrame")
        bottom_top.pack(fill=tk.X)

        ttk.Label(bottom_top, text="SON YAKALAMA DURUMU", style="Section.TLabel").pack(
            side=tk.LEFT
        )

        ttk.Button(
            bottom_top,
            text="LaTeX \\includegraphics Kodunu Kopyala",
            style="Secondary.TButton",
            command=self.copy_latex_snippet,
        ).pack(side=tk.RIGHT)

        self.status_var = tk.StringVar(
            value="Hazır. Print Screen ile tam ekran, Ctrl+Shift+S ile bölgesel ekran yakalayabilirsiniz."
        )
        ttk.Label(
            bottom_card,
            textvariable=self.status_var,
            style="Field.TLabel",
            wraplength=620,
        ).pack(anchor=tk.W, pady=(8, 4))

    def _sync_preferences(self) -> None:
        self.config_mgr.data["save_directory"] = self.dir_var.get().strip()
        self.config_mgr.data["auto_save"] = bool(self.auto_save_var.get())
        self.config_mgr.data["copy_to_clipboard"] = bool(self.clipboard_var.get())
        self.config_mgr.data["copy_latex_code"] = bool(self.latex_var.get())
        self.config_mgr.data["include_all_monitors"] = bool(self.all_monitors_var.get())
        self.config_mgr.save()

    def choose_directory(self) -> None:
        chosen = filedialog.askdirectory(
            initialdir=self.dir_var.get(),
            title="Ekran Görüntülerinin Kaydedileceği Klasörü Seçin",
        )
        if chosen:
            self.dir_var.set(chosen)
            self._sync_preferences()
            self.status_var.set(f"Kayıt klasörü güncellendi: {chosen}")

    def open_save_directory(self) -> None:
        folder = self.dir_var.get().strip()
        os.makedirs(folder, exist_ok=True)
        system = platform.system()
        try:
            if system == "Windows":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif system == "Darwin":
                subprocess.Popen(["open", folder])
            else:
                subprocess.Popen(["xdg-open", folder])
        except Exception as exc:
            messagebox.showerror("Hata", f"Klasör açılamadı:\n{exc}")

    def _register_global_hotkeys(self) -> None:
        if not HAS_KEYBOARD:
            self.status_var.set(
                "Not: Global kısayollar için 'pip install keyboard' önerilir. Pencere içi kısayollar aktif."
            )
            self.root.bind("<Print>", lambda e: self.start_fullscreen_capture())
            self.root.bind("<Control-Shift-S>", lambda e: self.start_region_capture())
            self.root.bind("<Control-Shift-s>", lambda e: self.start_region_capture())
            return

        try:
            keyboard.unhook_all_hotkeys()
        except Exception:
            pass

        hk_full = self.config_mgr.data["hotkey_fullscreen"]
        hk_reg = self.config_mgr.data["hotkey_region"]

        try:
            keyboard.add_hotkey(
                hk_full,
                lambda: self.root.after(0, self.start_fullscreen_capture),
            )
            keyboard.add_hotkey(
                hk_reg,
                lambda: self.root.after(0, self.start_region_capture),
            )
        except Exception as exc:
            self.status_var.set(f"Kısayol kaydı uyarısı: {exc}")

    def update_hotkeys(self) -> None:
        full_hk = self.hk_full_var.get().strip().lower()
        reg_hk = self.hk_region_var.get().strip().lower()
        if not full_hk or not reg_hk:
            messagebox.showwarning("Uyarı", "Kısayol alanları boş bırakılamaz.")
            return

        self.config_mgr.data["hotkey_fullscreen"] = full_hk
        self.config_mgr.data["hotkey_region"] = reg_hk
        self.config_mgr.save()

        self.btn_region.configure(text=f"✂ Bölgesel Ekran Yakala ({reg_hk.upper()})")
        self.btn_fullscreen.configure(text=f"🖥 Tam Ekran Yakala ({full_hk.upper()})")
        self._register_global_hotkeys()
        self.status_var.set(
            f"Kısayollar güncellendi: Tam Ekran [{full_hk.upper()}], Bölgesel [{reg_hk.upper()}]"
        )

    # -----------------------------------------------------------------------
    # YAKALAMA İŞ AKIŞLARI
    # -----------------------------------------------------------------------
    def trigger_fullscreen_capture_from_ui(self) -> None:
        """Arayüzdeki butona basıldığında pencereyi gizleyip tam ekran yakalar."""
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.18)
        self.start_fullscreen_capture(restore_window=True)

    def trigger_region_capture_from_ui(self) -> None:
        """Arayüzdeki butona basıldığında pencereyi gizleyip bölgesel yakalama başlatır."""
        self.root.withdraw()
        self.root.update_idletasks()
        time.sleep(0.18)
        self.start_region_capture(restore_window=True)

    def start_fullscreen_capture(self, restore_window: bool = False) -> None:
        if self.is_capturing:
            return
        self.is_capturing = True
        try:
            self._sync_preferences()
            img, _ = ScreenCaptureEngine.grab_raw_screen(
                all_monitors=self.config_mgr.data["include_all_monitors"]
            )
            self._process_captured_image(img, mode_label="Tam Ekran")
        finally:
            self.is_capturing = False
            if restore_window:
                self.root.deiconify()

    def start_region_capture(self, restore_window: bool = False) -> None:
        if self.is_capturing:
            return
        self.is_capturing = True
        self._sync_preferences()

        # Ekranı o anki orijinal pikselleriyle dondur
        frozen_img, bounds = ScreenCaptureEngine.grab_raw_screen(
            all_monitors=self.config_mgr.data["include_all_monitors"]
        )

        def _on_selected(cropped_img: Image.Image | None) -> None:
            self.is_capturing = False
            if restore_window:
                self.root.deiconify()
            if cropped_img is not None:
                self._process_captured_image(cropped_img, mode_label="Bölgesel")
            else:
                self.status_var.set("Bölgesel yakalama iptal edildi.")

        RegionSelectorOverlay(self.root, frozen_img, bounds, _on_selected)

    def _process_captured_image(self, image: Image.Image, mode_label: str) -> None:
        """Yakalanan orijinal çözünürlüklü görüntüyü kaydeder ve panoya kopyalar."""
        width, height = image.size
        details = [f"{mode_label}: {width}×{height} px (Kayıpsız PNG)"]

        if self.config_mgr.data["auto_save"]:
            saved_path = ScreenCaptureEngine.save_lossless_png(
                image=image,
                save_dir=self.config_mgr.data["save_directory"],
                prefix=self.config_mgr.data["filename_prefix"],
            )
            self.last_saved_path = saved_path
            details.append(f"Kaydedildi: {saved_path.name}")

        if self.config_mgr.data["copy_to_clipboard"]:
            ok = ScreenCaptureEngine.copy_image_to_clipboard(image)
            if ok:
                details.append("Panoya kopyalandı (Ctrl+V ile Word/LaTeX'e yapıştırabilirsiniz)")
            else:
                details.append("Pano kopyalaması başarısız oldu")

        self.status_var.set("  •  ".join(details))

    def copy_latex_snippet(self) -> None:
        """Son kaydedilen PNG dosyası için hazır LaTeX figure/includegraphics kodunu panoya kopyalar."""
        if not self.last_saved_path or not self.last_saved_path.exists():
            messagebox.showinfo(
                "Bilgi",
                "Henüz kaydedilmiş bir ekran görüntüsü yok. Önce bir ekran görüntüsü yakalayın.",
            )
            return

        posix_path = self.last_saved_path.as_posix()
        latex_code = (
            "\\begin{figure}[htbp]\n"
            "    \\centering\n"
            f"    \\includegraphics[width=\\textwidth]{{{posix_path}}}\n"
            "    \\caption{Ekran görüntüsü}\n"
            f"    \\label{{fig:{self.last_saved_path.stem}}}\n"
            "\\end{figure}"
        )
        self.root.clipboard_clear()
        self.root.clipboard_append(latex_code)
        self.root.update()
        self.status_var.set(f"LaTeX kodu panoya kopyalandı: {self.last_saved_path.name}")

    def on_close(self) -> None:
        self._sync_preferences()
        if HAS_KEYBOARD:
            try:
                keyboard.unhook_all_hotkeys()
            except Exception:
                pass
        self.root.destroy()


def main() -> None:
    root = tk.Tk()
    LosslessScreenshotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()