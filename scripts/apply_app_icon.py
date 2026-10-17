from pathlib import Path
from PIL import Image

SRC_PATH = Path(r"C:/Users/JUN/.gemini/antigravity/brain/38359003-684f-4c41-8646-99b83a7ff223/.user_uploaded/media_1792069532301_da11a16a.png")
RES_DIR = Path(r"D:/Jun_Project_D/The_Stock/android-app/app/src/main/res")

DENSITIES = {
    "mipmap-mdpi": (48, 48),
    "mipmap-hdpi": (72, 72),
    "mipmap-xhdpi": (96, 96),
    "mipmap-xxhdpi": (144, 144),
    "mipmap-xxxhdpi": (192, 192),
}

FOREGROUND_DENSITIES = {
    "mipmap-mdpi": (108, 108),
    "mipmap-hdpi": (162, 162),
    "mipmap-xhdpi": (216, 216),
    "mipmap-xxhdpi": (324, 324),
    "mipmap-xxxhdpi": (432, 432),
}

def main():
    img = Image.open(SRC_PATH).convert("RGBA")
    print(f"Loaded source image: {img.size}")

    # 1. 각 mipmap 폴더에 ic_launcher.png 및 ic_launcher_round.png 저장 (기존 webp 삭제)
    for folder, size in DENSITIES.items():
        target_dir = RES_DIR / folder
        target_dir.mkdir(parents=True, exist_ok=True)
        
        # 기존 webp 삭제
        for old in target_dir.glob("*.webp"):
            old.unlink()

        resized = img.resize(size, Image.Resampling.LANCZOS)
        resized.save(target_dir / "ic_launcher.png", "PNG")
        resized.save(target_dir / "ic_launcher_round.png", "PNG")
        print(f"Saved {folder}: {size}")

    # 2. drawable 폴더의 기존 foreground/background 벡터 교체
    drawable_dir = RES_DIR / "drawable"
    drawable_dir.mkdir(parents=True, exist_ok=True)

    # 432x432 크기의 adaptive icon foreground 저장
    fg_resized = img.resize((432, 432), Image.Resampling.LANCZOS)
    fg_resized.save(drawable_dir / "ic_launcher_foreground.png", "PNG")

    # 기존 ic_launcher_foreground.xml 파일 삭제
    old_fg_xml = drawable_dir / "ic_launcher_foreground.xml"
    if old_fg_xml.exists():
        old_fg_xml.unlink()

    # background를 흰색(#FFFFFF)으로 깔끔하게 설정
    bg_xml_content = """<?xml version="1.0" encoding="utf-8"?>
<vector xmlns:android="http://schemas.android.com/apk/res/android"
    android:width="108dp"
    android:height="108dp"
    android:viewportWidth="108"
    android:viewportHeight="108">
    <path
        android:fillColor="#F0F4F8"
        android:pathData="M0,0h108v108h-108z" />
</vector>
"""
    (drawable_dir / "ic_launcher_background.xml").write_text(bg_xml_content, encoding="utf-8")

    # 3. mipmap-anydpi-v26 XML 설정
    anydpi_dir = RES_DIR / "mipmap-anydpi-v26"
    anydpi_dir.mkdir(parents=True, exist_ok=True)

    adaptive_xml = """<?xml version="1.0" encoding="utf-8"?>
<adaptive-icon xmlns:android="http://schemas.android.com/apk/res/android">
    <background android:drawable="@drawable/ic_launcher_background" />
    <foreground android:drawable="@drawable/ic_launcher_foreground" />
    <monochrome android:drawable="@drawable/ic_launcher_foreground" />
</adaptive-icon>
"""
    (anydpi_dir / "ic_launcher.xml").write_text(adaptive_xml, encoding="utf-8")
    (anydpi_dir / "ic_launcher_round.xml").write_text(adaptive_xml, encoding="utf-8")
    print("Updated adaptive icon XMLs.")

if __name__ == "__main__":
    main()
