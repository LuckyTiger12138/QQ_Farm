"""从GitHub项目导入种子图片作为模板"""
import os
import re
import time
import requests
from PIL import Image
from io import BytesIO

# GitHub 仓库信息
GITHUB_REPO = "Penty-d/qq-farm-bot-ui"
SEED_IMAGES_PATH = "core/src/gameConfig/seed_images_named"

# 目标目录
DST_DIR = os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "templates")

# 直接的GitHub文件URL模板
GITHUB_RAW_URL = f"https://raw.githubusercontent.com/{GITHUB_REPO}/main/{SEED_IMAGES_PATH}"

# GitHub目录页面URL
GITHUB_DIR_URL = f"https://github.com/{GITHUB_REPO}/tree/main/{SEED_IMAGES_PATH}"

# 代理设置（使用clash代理）
PROXIES = {
    "http": "http://127.0.0.1:7890",
    "https": "http://127.0.0.1:7890"
}


def get_seed_files_from_github():
    """从GitHub目录页面获取所有种子图片文件名"""
    print(f"从 GitHub 目录页面获取种子文件列表: {GITHUB_DIR_URL}")
    
    try:
        response = requests.get(GITHUB_DIR_URL, timeout=15, proxies=PROXIES)
        response.raise_for_status()
        html_content = response.text
        
        # 解析HTML，提取所有种子图片文件名
        # GitHub目录页面的文件链接格式: <a href="/Penty-d/qq-farm-bot-ui/blob/main/core/src/gameConfig/seed_images_named/20002_白萝卜_Crop_2_Seed.png" class="...">
        pattern = r'/Penty-d/qq-farm-bot-ui/blob/main/core/src/gameConfig/seed_images_named/([^"<>]+\.png)'
        matches = re.findall(pattern, html_content)
        
        if matches:
            print(f"找到 {len(matches)} 个种子图片文件")
            return matches
        else:
            print("未找到种子图片文件")
            return []
            
    except Exception as e:
        print(f"获取文件列表失败: {e}")
        return []


def download_file(url, filename, retries=3):
    """下载文件并保存到本地，支持重试"""
    for i in range(retries):
        try:
            response = requests.get(url, stream=True, timeout=10, proxies=PROXIES)
            response.raise_for_status()
            return Image.open(BytesIO(response.content))
        except Exception as e:
            print(f"  ✗ 下载失败 {filename} (尝试 {i+1}/{retries}): {e}")
            if i < retries - 1:
                time.sleep(2)  # 等待2秒后重试
            else:
                return None


def main():
    os.makedirs(DST_DIR, exist_ok=True)

    print(f"从 GitHub 仓库 {GITHUB_REPO} 下载种子图片...")
    print(f"使用直接的 GitHub Raw URL: {GITHUB_RAW_URL}")

    # 从GitHub目录页面获取种子文件列表
    SEED_FILES = get_seed_files_from_github()
    if not SEED_FILES:
        print("无法获取种子文件列表，任务失败")
        return

    count = 0
    for filename in SEED_FILES:
        # 跳过变异作物和狗粮
        if "Mutant" in filename or "dog_food" in filename:
            continue

        # 解析文件名: 20002_白萝卜_Crop_2_Seed.png → seed_白萝卜
        # 或: Crop_101_Seed.png → seed_crop101
        import urllib.parse
        match = re.match(r"(\d+)_(.+?)_Crop_\d+_Seed", filename)
        if match:
            seed_id = match.group(1)
            name = match.group(2)
            # 解码URL编码的中文
            name = urllib.parse.unquote(name)
            seed_dst_name = f"seed_{name}.png"
            shop_dst_name = f"shop_{name}.png"
        else:
            match2 = re.match(r"Crop_(\d+)_Seed", filename)
            if match2:
                crop_id = match2.group(1)
                seed_dst_name = f"seed_crop{crop_id}.png"
                shop_dst_name = f"shop_crop{crop_id}.png"
            else:
                continue

        # 构建直接的下载URL
        download_url = f"{GITHUB_RAW_URL}/{filename}"

        # 下载图片
        img = download_file(download_url, filename)
        if img:
            try:
                img = img.convert("RGBA")
                
                # 保存seed_开头的模板
                seed_dst_path = os.path.join(DST_DIR, seed_dst_name)
                if not os.path.exists(seed_dst_path):
                    img.save(seed_dst_path)
                    count += 1
                    print(f"  ✓ {filename} → {seed_dst_name} ({img.size[0]}x{img.size[1]})")
                else:
                    print(f"  ⏩ {seed_dst_name} 已存在，跳过")
                
                # 保存shop_开头的模板
                shop_dst_path = os.path.join(DST_DIR, shop_dst_name)
                if not os.path.exists(shop_dst_path):
                    img.save(shop_dst_path)
                    count += 1
                    print(f"  ✓ {filename} → {shop_dst_name} ({img.size[0]}x{img.size[1]})")
                else:
                    print(f"  ⏩ {shop_dst_name} 已存在，跳过")
                    
            except Exception as e:
                print(f"  ✗ 保存失败 {filename}: {e}")
        
        # 避免过快的请求
        time.sleep(0.5)

    print(f"\n导入完成，共 {count} 个种子模板 → {DST_DIR}")


if __name__ == "__main__":
    main()
