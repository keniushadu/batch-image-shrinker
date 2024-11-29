#!/usr/bin/env python3
import os
import sys
from PIL import Image
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import multiprocessing

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 创建一个锁用于同步日志输出
log_lock = Lock()

class CompressionStats:
    def __init__(self):
        self.lock = Lock()
        self.processed_count = 0
        self.skipped_count = 0
        self.total_original_size = 0
        self.total_compressed_size = 0
        self.resolution_stats = {}  # 添加分辨率统计

    def update(self, original_size, compressed_size, resolution=None):
        with self.lock:
            if original_size and compressed_size:
                self.processed_count += 1
                self.total_original_size += original_size
                self.total_compressed_size += compressed_size
                if resolution:
                    self.resolution_stats[resolution] = self.resolution_stats.get(resolution, 0) + 1
            else:
                self.skipped_count += 1

def get_compressed_filename(filepath: str) -> str:
    """
    生成压缩后的文件名
    
    Args:
        filepath: 原始文件路径
        
    Returns:
        str: 压缩后的文件路径
    """
    directory = os.path.dirname(filepath)
    filename = os.path.basename(filepath)
    name, ext = os.path.splitext(filename)
    return os.path.join(directory, f"{name}_min{ext}")

def analyze_image_resolutions(directory_path: str) -> dict:
    """
    分析目录中所有图片的分辨率分布
    
    Args:
        directory_path: 目录路径
        
    Returns:
        dict: 分辨率统计信息
    """
    stats = CompressionStats()
    
    def process_image(filepath):
        try:
            with Image.open(filepath) as img:
                resolution = f"{img.width}x{img.height}"
                stats.update(1, 1, resolution)  # 使用虚拟大小，主要关注分辨率统计
        except Exception as e:
            with log_lock:
                logger.error(f"处理文件 {filepath} 时出错: {str(e)}")
    
    with ThreadPoolExecutor(max_workers=multiprocessing.cpu_count()) as executor:
        futures = []
        for root, _, files in os.walk(directory_path):
            for filename in files:
                if filename.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                    filepath = os.path.join(root, filename)
                    futures.append(executor.submit(process_image, filepath))
        
        for future in as_completed(futures):
            future.result()
    
    # 按照图片数量排序并打印统计信息
    sorted_stats = sorted(stats.resolution_stats.items(), key=lambda x: x[1], reverse=True)
    total_images = sum(count for _, count in sorted_stats)
    
    logger.info("\n分辨率分布统计:")
    for resolution, count in sorted_stats:
        percentage = (count / total_images) * 100
        logger.info(f"{resolution}: {count} 张图片 ({percentage:.1f}%)")
    
    return stats.resolution_stats

def resize_image(image: Image.Image, target_width: int = None, target_height: int = None, scale_ratio: float = 0.5) -> Image.Image:
    """
    调整图片分辨率，当图片分辨率超过目标分辨率时，按照给定比例压缩
    
    Args:
        image: PIL Image对象
        target_width: 目标宽度
        target_height: 目标高度
        scale_ratio: 压缩比例（0-1之间），默认0.5表示压缩到超出部分的一半
        
    Returns:
        Image.Image: 调整后的图片
    """
    if not target_width and not target_height:
        return image
    
    original_width, original_height = image.size
    
    # 如果指定了宽度和高度
    if target_width and target_height:
        # 只有当原始尺寸超过目标尺寸时才调整
        if original_width <= target_width and original_height <= target_height:
            return image
            
        # 计算宽度和高度的压缩比例
        width_ratio = (original_width - target_width) * scale_ratio + target_width
        height_ratio = (original_height - target_height) * scale_ratio + target_height
        
        # 保持宽高比，选择较小的缩放比例
        scale = min(width_ratio / original_width, height_ratio / original_height)
        new_size = (int(original_width * scale), int(original_height * scale))
        
    # 如果只指定了宽度
    elif target_width:
        # 只有当原始宽度超过目标宽度时才调整
        if original_width <= target_width:
            return image
            
        # 计算新的宽度
        new_width = (original_width - target_width) * scale_ratio + target_width
        # 保持宽高比
        scale = new_width / original_width
        new_size = (int(new_width), int(original_height * scale))
        
    # 如果只指定了高度
    else:  # target_height
        # 只有当原始高度超过目标高度时才调整
        if original_height <= target_height:
            return image
            
        # 计算新的高度
        new_height = (original_height - target_height) * scale_ratio + target_height
        # 保持宽高比
        scale = new_height / original_height
        new_size = (int(original_width * scale), int(new_height))
    
    # 使用LANCZOS算法进行高质量的图片缩放
    resized_image = image.resize(new_size, Image.Resampling.LANCZOS)
    
    with log_lock:
        logger.info(f"调整分辨率: {original_width}x{original_height} -> {new_size[0]}x{new_size[1]}")
        logger.info(f"压缩比例: {scale:.2f}")
    
    return resized_image

def compress_image(input_path: str, quality: int = 50, target_width: int = None, target_height: int = None, scale_ratio: float = 0.5) -> tuple:
    """
    压缩单个图片，保存为新文件
    
    Args:
        input_path: 输入图片路径
        quality: 压缩质量（1-100）
        target_width: 目标宽度
        target_height: 目标高度
        scale_ratio: 压缩比例（0-1之间）
        
    Returns:
        tuple: (原始大小, 压缩后大小) 如果压缩后文件更大则返回 None
    """
    try:
        # 获取原始文件大小
        original_size = os.path.getsize(input_path)
        output_path = get_compressed_filename(input_path)
        
        with Image.open(input_path) as img:
            # 保存原始格式和模式
            original_format = img.format
            original_mode = img.mode
            
            # 如果需要调整分辨率
            if target_width or target_height:
                img = resize_image(img, target_width, target_height, scale_ratio)
            
            # 根据图片模式选择保存参数
            save_args = {'quality': quality, 'optimize': True}
            
            # 处理不同格式的特殊情况
            if original_format == 'PNG':
                if original_mode == 'RGBA':
                    # PNG with alpha channel
                    save_args = {
                        'optimize': True,
                        'quality': quality,
                        'format': 'PNG'
                    }
                else:
                    # PNG without alpha channel
                    save_args = {
                        'optimize': True,
                        'quality': quality,
                        'format': 'PNG'
                    }
            elif original_format == 'JPEG':
                save_args = {
                    'quality': quality,
                    'optimize': True,
                    'format': 'JPEG'
                }
            elif original_format == 'WEBP':
                save_args = {
                    'quality': quality,
                    'format': 'WEBP',
                    'lossless': original_mode == 'RGBA'  # 如果有Alpha通道，使用无损压缩
                }
            
            # 保存压缩后的图片
            img.save(output_path, **save_args)
            
            # 检查压缩后的大小
            compressed_size = os.path.getsize(output_path)
            
            # 如果压缩后的文件更大，删除压缩后的文件并返回None
            if compressed_size >= original_size:
                os.remove(output_path)
                with log_lock:
                    logger.info(f"跳过 {input_path}: 压缩后文件更大")
                return None, None
            
            with log_lock:
                logger.info(f"处理: {input_path}")
                logger.info(f"格式: {original_format}, 模式: {original_mode}")
                logger.info(f"原始大小: {original_size/1024:.1f}KB")
                logger.info(f"压缩后大小: {compressed_size/1024:.1f}KB")
                logger.info(f"压缩率: {(1 - compressed_size/original_size)*100:.1f}%")
            
            return original_size, compressed_size
            
    except Exception as e:
        with log_lock:
            logger.error(f"处理文件 {input_path} 时出错: {str(e)}")
        if os.path.exists(output_path):
            os.remove(output_path)
        return None, None

def process_directory(directory_path: str, quality: int = 50, target_width: int = None, target_height: int = None, scale_ratio: float = 0.5) -> None:
    """
    处理目录中的所有图片
    
    Args:
        directory_path: 目录路径
        quality: 压缩质量（1-100）
        target_width: 目标宽度
        target_height: 目标高度
        scale_ratio: 压缩比例（0-1之间）
    """
    supported_formats = {'.jpg', '.jpeg', '.png'}
    stats = CompressionStats()
    
    try:
        # 收集所有需要处理的文件
        files_to_process = []
        for root, _, files in os.walk(directory_path):
            for filename in files:
                # 检查文件扩展名
                file_ext = os.path.splitext(filename)[1].lower()
                if file_ext not in supported_formats:
                    continue
                
                # 跳过已经压缩过的文件（带有_min后缀的文件）
                if '_min.' in filename:
                    continue
                
                # 构建完整的文件路径
                input_path = os.path.join(root, filename)
                files_to_process.append(input_path)
        
        # 获取CPU核心数，用于设置线程池大小
        max_workers = max(multiprocessing.cpu_count() - 1, 1)  # 保留一个核心给系统
        with log_lock:
            logger.info(f"使用 {max_workers} 个线程进行并行处理")
        
        # 使用线程池并行处理图片
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            # 提交所有任务
            future_to_file = {
                executor.submit(compress_image, f, quality, target_width, target_height, scale_ratio): f 
                for f in files_to_process
            }
            
            # 处理完成的任务
            for future in as_completed(future_to_file):
                original_size, compressed_size = future.result()
                stats.update(original_size, compressed_size)
        
        # 输出最终统计信息
        if stats.processed_count > 0:
            total_ratio = (1 - stats.total_compressed_size / stats.total_original_size) * 100
            with log_lock:
                logger.info(f"压缩完成统计:")
                logger.info(f"成功处理文件数: {stats.processed_count}")
                logger.info(f"跳过文件数（压缩无效）: {stats.skipped_count}")
                logger.info(f"总原始大小: {stats.total_original_size/1024/1024:.2f}MB")
                logger.info(f"总压缩后大小: {stats.total_compressed_size/1024/1024:.2f}MB")
                logger.info(f"平均压缩率: {total_ratio:.2f}%")
        else:
            with log_lock:
                logger.info(f"没有找到需要处理的图片文件")
                if stats.skipped_count > 0:
                    logger.info(f"跳过的文件数: {stats.skipped_count}")
            
    except Exception as e:
        with log_lock:
            logger.error(f"处理目录时发生错误: {str(e)}")

def replace_with_compressed(directory_path: str) -> None:
    """
    用压缩后的文件替换原文件
    
    Args:
        directory_path: 目录路径
    """
    try:
        replaced_count = 0
        for root, _, files in os.walk(directory_path):
            for filename in files:
                if '_min.' in filename:
                    # 构建压缩文件和原始文件的路径
                    compressed_path = os.path.join(root, filename)
                    original_name = filename.replace('_min.', '.')
                    original_path = os.path.join(root, original_name)
                    
                    if os.path.exists(original_path):
                        # 备份原文件
                        backup_path = original_path + '.backup'
                        os.rename(original_path, backup_path)
                        
                        try:
                            # 将压缩文件重命名为原文件名
                            os.rename(compressed_path, original_path)
                            # 删除备份
                            os.remove(backup_path)
                            replaced_count += 1
                            with log_lock:
                                logger.info(f"已替换: {original_name}")
                        except Exception as e:
                            # 如果出错，恢复备份
                            if os.path.exists(backup_path):
                                os.rename(backup_path, original_path)
                            with log_lock:
                                logger.error(f"替换文件 {original_name} 时发生错误: {str(e)}")
        
        with log_lock:
            if replaced_count > 0:
                logger.info(f"替换完成，共替换了 {replaced_count} 个文件")
            else:
                logger.info("没有找到可替换的文件")
                
    except Exception as e:
        with log_lock:
            logger.error(f"替换过程中发生错误: {str(e)}")

def main():
    if len(sys.argv) < 2:
        print("使用方法:")
        print("分析分辨率: python image_compressor.py analyze <目录路径>")
        print("压缩图片: python image_compressor.py compress <目录路径> [质量(1-100)] [目标宽度] [目标高度] [压缩比例(0-1)]")
        print("替换原文件: python image_compressor.py replace <目录路径>")
        sys.exit(1)

    command = sys.argv[1]
    directory_path = sys.argv[2] if len(sys.argv) > 2 else "."

    if command == "analyze":
        analyze_image_resolutions(directory_path)
    elif command == "compress":
        quality = int(sys.argv[3]) if len(sys.argv) > 3 else 50
        target_width = int(sys.argv[4]) if len(sys.argv) > 4 else None
        target_height = int(sys.argv[5]) if len(sys.argv) > 5 else None
        scale_ratio = float(sys.argv[6]) if len(sys.argv) > 6 else 0.5
        
        if scale_ratio < 0 or scale_ratio > 1:
            print("压缩比例必须在0-1之间")
            sys.exit(1)
            
        process_directory(directory_path, quality, target_width, target_height, scale_ratio)
    elif command == "replace":
        replace_with_compressed(directory_path)
    else:
        print("未知命令。请使用 analyze, compress 或 replace")
        sys.exit(1)

if __name__ == "__main__":
    main()
