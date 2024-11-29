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

    def update(self, original_size, compressed_size):
        with self.lock:
            if original_size and compressed_size:
                self.processed_count += 1
                self.total_original_size += original_size
                self.total_compressed_size += compressed_size
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

def compress_image(input_path: str, quality: int = 50) -> tuple:
    """
    压缩单个图片，保存为新文件
    
    Args:
        input_path: 输入图片路径
        quality: 压缩质量（1-100）
        
    Returns:
        tuple: (原始大小, 压缩后大小) 如果压缩后文件更大则返回 None
    """
    try:
        # 获取原始文件大小
        original_size = os.path.getsize(input_path)
        
        # 生成压缩后的文件路径
        output_path = get_compressed_filename(input_path)
        
        # 打开并压缩图片
        with Image.open(input_path) as img:
            # 保持原始格式
            img_format = img.format
            
            # 根据不同格式采用不同的压缩策略
            if img_format == 'PNG':
                # 对于PNG，尝试保持原始模式
                if 'A' in img.mode:  # 如果有透明通道
                    # 使用RGBA模式
                    img_to_save = img
                else:
                    # 如果没有透明通道，转换为RGB
                    img_to_save = img.convert('RGB')
                # PNG特定的优化参数
                img_to_save.save(output_path, format=img_format, optimize=True,
                               quality=quality)
            else:
                # JPG/JPEG的处理
                if img.mode in ('RGBA', 'P'):
                    img_to_save = img.convert('RGB')
                else:
                    img_to_save = img
                # JPEG特定的优化参数
                img_to_save.save(output_path, format=img_format, quality=quality,
                               optimize=True, progressive=True)
        
        # 获取压缩后文件大小
        compressed_size = os.path.getsize(output_path)
        
        # 如果压缩后的文件更大，删除压缩文件并返回None
        if compressed_size >= original_size:
            os.remove(output_path)
            with log_lock:
                logger.info(f"跳过 {os.path.basename(input_path)} - 压缩后文件更大")
            return None, None
        
        with log_lock:
            compression_ratio = (1 - compressed_size / original_size) * 100
            logger.info(f"压缩完成: {os.path.basename(input_path)}")
            logger.info(f"压缩后文件: {os.path.basename(output_path)}")
            logger.info(f"原始大小: {original_size/1024:.2f}KB")
            logger.info(f"压缩后大小: {compressed_size/1024:.2f}KB")
            logger.info(f"压缩率: {compression_ratio:.2f}%")
            logger.info("-" * 50)
        
        return original_size, compressed_size
        
    except Exception as e:
        with log_lock:
            logger.error(f"处理图片 {input_path} 时发生错误: {str(e)}")
        # 如果发生错误，确保删除可能存在的输出文件
        if 'output_path' in locals() and os.path.exists(output_path):
            os.remove(output_path)
        return None, None

def process_directory(directory_path: str, quality: int = 50) -> None:
    """
    处理目录中的所有图片
    
    Args:
        directory_path: 目录路径
        quality: 压缩质量（1-100）
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
                executor.submit(compress_image, f, quality): f 
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
        print("压缩图片: python image_compressor.py compress <目录路径> [压缩质量(1-100)]")
        print("替换原文件: python image_compressor.py replace <目录路径>")
        return

    command = sys.argv[1]
    if command not in ['compress', 'replace']:
        print("无效的命令。请使用 'compress' 或 'replace'")
        return

    directory_path = sys.argv[2] if len(sys.argv) > 2 else '.'
    
    if not os.path.isdir(directory_path):
        print(f"错误：'{directory_path}' 不是有效的目录")
        return

    if command == 'compress':
        # 获取压缩质量，默认为50
        quality = int(sys.argv[3]) if len(sys.argv) > 3 else 50
        if quality < 1 or quality > 100:
            print("压缩质量必须在1-100之间")
            return
        process_directory(directory_path, quality)
    else:  # replace
        replace_with_compressed(directory_path)

if __name__ == "__main__":
    main()
