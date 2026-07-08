# core/wechat_model_downloader.py
# Utility to download WeChat QR Code detector models if they are missing.

import os
import urllib.request
import logging
from config import WECHAT_QR_MODEL_DIR, WECHAT_QR_MODEL_URLS

logger = logging.getLogger(__name__)


def ensure_wechat_models() -> bool:
    """
    Checks if all 4 required WeChat QR model files exist in the model directory.
    If any are missing, it attempts to download them from GitHub.
    Returns:
        True if all files exist or are successfully downloaded.
        False if download/setup fails.
    """
    try:
        # Create directory if it does not exist
        if not os.path.exists(WECHAT_QR_MODEL_DIR):
            os.makedirs(WECHAT_QR_MODEL_DIR, exist_ok=True)
            logger.info(f"Membuat direktori model WeChat QR: {WECHAT_QR_MODEL_DIR}")

        missing_files = []
        for filename in WECHAT_QR_MODEL_URLS.keys():
            filepath = os.path.join(WECHAT_QR_MODEL_DIR, filename)
            if not os.path.exists(filepath) or os.path.getsize(filepath) == 0:
                missing_files.append(filename)

        if not missing_files:
            logger.debug("Semua file model WeChat QR lengkap.")
            return True

        logger.info(f"Menemukan {len(missing_files)} model WeChat QR yang perlu diunduh: {missing_files}")
        
        # Download missing files
        for filename in missing_files:
            url = WECHAT_QR_MODEL_URLS[filename]
            filepath = os.path.join(WECHAT_QR_MODEL_DIR, filename)
            logger.info(f"Mengunduh {filename} dari {url} ...")
            
            # Simple download using urllib with headers
            req = urllib.request.Request(
                url, 
                headers={'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'}
            )
            with urllib.request.urlopen(req, timeout=30) as response, open(filepath, 'wb') as out_file:
                out_file.write(response.read())
            logger.info(f"Selesai mengunduh {filename}")

        return True
    except Exception as e:
        logger.exception(f"Gagal memastikan file model WeChat QR: {e}")
        return False


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    success = ensure_wechat_models()
    print(f"Status download model: {success}")
