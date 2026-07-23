"""標本影像上傳驗證。

目的：在檔案送往 Cloudinary「之前」，先擋掉會被 Cloudinary 拒絕或無法轉換的檔案，
避免正式環境出現 500。

限制（刻意）：
- 只做驗證，不做任何伺服器端縮圖或壓縮。Render 免費方案記憶體僅 512 MB，
  影像壓縮改由使用者在上傳前自行完成（作業規範另訂）。
- 讀取尺寸時以 PIL.Image.open() 只讀檔頭取得 size，不解碼整張圖，避免大圖把
  記憶體吃光。
- 讀完務必 file.seek(0) 把指標歸零，否則後續上傳到 Cloudinary 會拿到空檔案。
"""

from django.core.exceptions import ValidationError
from PIL import Image, UnidentifiedImageError

# Cloudinary 免費方案：上傳上限 10 MB、轉換像素上限 25 megapixels。
# 檔案大小刻意取比 10 MB 更保守的 5 MB（含 multipart 開銷仍有餘裕）。
MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_MEGAPIXELS = 25

# 「無法讀取影像」訊息。此常數同時給：
# (1) 本模組情況 (c)（model 層級驗證）；
# (2) admin ModelForm 覆寫 forms.ImageField 的 invalid_image 訊息。
# 兩條路徑共用同一份文字，確保使用者看到的訊息完全一致。
INVALID_IMAGE_MESSAGE = (
    "無法讀取此影像檔。可能原因：(1) 格式為 HEIC 等系統不支援的格式，"
    "(2) 檔案損毀。請先轉存為 JPEG（長邊 2000 px、品質 85）後再上傳，"
    "詳見「標本影像作業規範」。"
)


def validate_specimen_image(file):
    """驗證上傳影像；任一項不通過即 raise ValidationError（繁中、含處理指引）。

    檢查三件事：
    (a) 檔案大小不得超過 MAX_UPLOAD_BYTES
    (b) 寬 × 高 不得超過 MAX_MEGAPIXELS
    (c) 檔案必須能被 Pillow 開啟（HEIC 等無法開啟者擋下）
    """
    # (a) 檔案大小
    size = getattr(file, "size", None)
    if size is not None and size > MAX_UPLOAD_BYTES:
        raise ValidationError(
            "影像檔案過大（目前 %(current).1f MB，上限 %(limit).0f MB）。"
            "請先將長邊縮至 2000 px、以 JPEG 品質 85 轉存後再上傳，"
            "詳見「標本影像作業規範」。"
            % {
                "current": size / (1024 * 1024),
                "limit": MAX_UPLOAD_BYTES / (1024 * 1024),
            }
        )

    # (b)(c) 以 PIL 只讀檔頭取得尺寸（不呼叫 load()，不解碼整張圖）
    try:
        file.seek(0)
        with Image.open(file) as img:
            width, height = img.size
    except (UnidentifiedImageError, OSError, ValueError):
        # 無法開啟（例如 HEIC）——與 admin invalid_image 訊息一致
        raise ValidationError(INVALID_IMAGE_MESSAGE)
    finally:
        # 不論成功或失敗，一律把指標歸零，確保後續上傳拿到完整檔案
        try:
            file.seek(0)
        except (OSError, ValueError):
            pass

    megapixels = (width * height) / 1_000_000
    if megapixels > MAX_MEGAPIXELS:
        raise ValidationError(
            "影像像素過高（目前 %(current).1f 百萬畫素，上限 %(limit).0f 百萬畫素）。"
            "請先將長邊縮至 2000 px 後再上傳。"
            % {"current": megapixels, "limit": MAX_MEGAPIXELS}
        )
