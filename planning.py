"""
课程计划解析器 - 自动从Google Sheets生成ICS日历文件

功能特性:
- 动态提取月份URL
- 自动检测删除线课程(CSS类和内联样式)  
- 支持配置文件手动取消
- 生成标准ICS格式日历文件
- 智能缓存机制
- 统一日志系统

作者: Assistant
版本: 5.1 (优化版)
日期: 2025-10-06
"""

import requests
import csv
import re
from ics import Calendar, Event
from datetime import datetime
import os
from zoneinfo import ZoneInfo
from io import StringIO
from bs4 import BeautifulSoup
from typing import Set, List, Dict, Tuple, Optional, Union, NamedTuple, ClassVar
import logging
from dataclasses import dataclass
from functools import lru_cache
import hashlib
from urllib.parse import urlparse
import time
from contextlib import contextmanager

# 配置常量
class Config:
    # 核心URL和文件配置
    PLANNING_URL = "https://handiman.univ-paris8.fr/planningM2-25-26.php"
    OUTPUT_FILENAME = "master_handi_schedule.ics"
    CONFIG_FILE = "cancelled_dates.txt"
    TIMEZONE = "Europe/Paris"
    
    # 网络请求配置
    REQUEST_TIMEOUT = 20
    MAX_RETRIES = 3
    CACHE_SIZE = 128
    
    # HTML检测配置
    STRIKETHROUGH_PATTERNS: ClassVar[List[str]] = [
        r'text-decoration:\s*line-through',
        r'text-decoration-line:\s*line-through',
    ]
    
    # 时间解析配置
    # 支持 h 和 : 作为分隔符
    TIME_PATTERN = r'(\d{1,2}(?:h|:)(?:\d{2})?)\s*-\s*(\d{1,2}(?:h|:)(?:\d{2})?)'
    DATE_PATTERN = r'(\d{1,2})/(\d{1,2})(?:/(\d{4}))?'
    
    # 支持的月份名称（含字符标准化映射）
    SUPPORTED_MONTHS = {
        'septembre', 'octobre', 'novembre', 'decembre', 'janvier', 'fevrier',
        'mars', 'avril', 'mai', 'juin', 'juillet'
    }
    
    MONTH_NORMALIZATION = {
        'é': 'e', 'è': 'e', 'ç': 'c', 'à': 'a', 'ù': 'u', 'ô': 'o'
    }
    
    # Webcal订阅更新周期配置
    ENABLE_REFRESH_INTERVAL = True
    REFRESH_INTERVAL_HOURS = 1  # 每小时检查更新
    PUBLISH_TTL_HOURS = 1       # 1小时缓存TTL
    CALENDAR_METHOD = "PUBLISH" # 发布模式
    
    # 标准请求头
    HEADERS = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
        'Accept-Language': 'en-US,en;q=0.5',
        'Accept-Encoding': 'gzip, deflate',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
    }

@dataclass(frozen=True)
class CancelledEvent:
    """取消的事件信息"""
    date: Tuple[int, int, int]  # (year, month, day)
    content: str
    event_type: str  # 'full' 或 'partial'

class ParsedTime(NamedTuple):
    """解析的时间信息"""
    hour: int
    minute: int

@contextmanager
def timer(operation: str):
    """性能计时器上下文管理器"""
    start_time = time.time()
    try:
        yield
    finally:
        elapsed = time.time() - start_time
        logger.info(f"⏱️ {operation} 耗时: {elapsed:.2f}秒")

# 配置统一日志系统
logger = logging.getLogger(__name__)
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter(
        '%(levelname)s: %(message)s',
        datefmt='%H:%M:%S'
    )
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)

@lru_cache(maxsize=Config.CACHE_SIZE)
def extract_month_urls(planning_url: str = None) -> Optional[Dict[str, str]]:
    """
    从主页面提取所有月份和对应的Google Sheets URL (带缓存)
    
    Args:
        planning_url: 主页面URL，默认使用配置中的URL
        
    Returns:
        Dict[str, str]: 月份名称到CSV URL的映射，失败时返回None
    """
    if planning_url is None:
        planning_url = Config.PLANNING_URL
        
    logger.info("正在从主页面提取月份信息...")
    
    for attempt in range(Config.MAX_RETRIES):
        try:
            response = requests.get(
                planning_url, 
                headers=Config.HEADERS, 
                timeout=Config.REQUEST_TIMEOUT
            )
            response.raise_for_status()
            
            html_content = response.text
            
            # 提取iframe源链接
            iframe_pattern = r'<iframe[^>]*src=[\'"]([^\'">]*pubhtml[^\'">]*)[\'"][^>]*>'
            matches = re.findall(iframe_pattern, html_content)
            
            # 提取月份标签
            label_pattern = r'<label[^>]*>([^<]+)</label>'
            month_labels = re.findall(label_pattern, html_content)
            
            # 过滤和标准化月份名称
            month_names = []
            for label in month_labels:
                # 使用配置中的标准化映射
                normalized_month = label.lower()
                for old_char, new_char in Config.MONTH_NORMALIZATION.items():
                    normalized_month = normalized_month.replace(old_char, new_char)
                    
                if normalized_month in Config.SUPPORTED_MONTHS:
                    month_names.append(normalized_month)
            
            if len(matches) == len(month_names):
                urls = {}
                for month, html_url in zip(month_names, matches):
                    # 转换HTML URL为CSV URL
                    csv_url = html_url.replace('/pubhtml?', '/pub?') + '&output=csv'
                    urls[month] = csv_url
                    logger.info(f"找到: {month} -> {csv_url[:60]}...")
                
                return urls
            else:
                logger.warning(f"月份数量({len(month_names)})与链接数量({len(matches)})不匹配")
                return None
                
        except requests.RequestException as e:
            if attempt < Config.MAX_RETRIES - 1:
                logger.warning(f"网络请求失败 (尝试 {attempt + 1}/{Config.MAX_RETRIES}): {e}")
                continue
            else:
                logger.error(f"网络请求最终失败: {e}")
                return None
        except Exception as e:
            logger.error(f"提取月份信息失败: {e}")
            return None

def parse_date_string(date_str: str) -> Optional[Tuple[int, int, int]]:
    """
    解析日期字符串为(year, month, day)元组
    
    Args:
        date_str: 日期字符串，支持格式 DD/MM 或 DD/MM/YYYY
        
    Returns:
        Tuple[int, int, int]: (year, month, day)，解析失败返回None
    """
    date_match = re.match(Config.DATE_PATTERN, date_str.strip())
    if date_match:
        day = int(date_match.group(1))
        month = int(date_match.group(2))
        # 1月到7月属于2026年（春季学期），8月到12月属于2025年（秋季学期）
        year = int(date_match.group(3)) if date_match.group(3) else (2026 if 1 <= month <= 7 else 2025)
        
        # 基本日期验证
        if not (1 <= day <= 31 and 1 <= month <= 12):
            logger.warning(f"无效日期: {day:02d}/{month:02d}/{year}")
            return None
            
        return (year, month, day)
    return None

def load_cancelled_dates(config_file: str = None) -> Set[Tuple[int, int, int]]:
    """
    从配置文件加载被取消的日期
    
    Args:
        config_file: 配置文件路径，默认使用Config.CONFIG_FILE
        
    Returns:
        Set[Tuple[int, int, int]]: 被取消日期的集合 (year, month, day)
    """
    if config_file is None:
        config_file = Config.CONFIG_FILE
        
    cancelled_dates = set()
    
    try:
        if os.path.exists(config_file):
            with open(config_file, 'r', encoding='utf-8') as f:
                for line_num, line in enumerate(f, 1):
                    line = line.strip()
                    if line and not line.startswith('#'):
                        parsed_date = parse_date_string(line)
                        if parsed_date:
                            year, month, day = parsed_date
                            cancelled_dates.add((year, month, day))
                            logger.info(f"配置: 将跳过 {day:02d}/{month:02d}/{year} 的课程")
                        else:
                            logger.warning(f"配置文件第{line_num}行日期格式错误: {line}")
        else:
            logger.info(f"未找到配置文件 {config_file}，不跳过任何日期")
    
    except FileNotFoundError:
        logger.info(f"配置文件 {config_file} 不存在，不跳过任何日期")
    except PermissionError:
        logger.error(f"没有权限读取配置文件 {config_file}")
    except Exception as e:
        logger.error(f"读取配置文件失败: {e}")
    
    return cancelled_dates

def detect_strikethrough_from_html(csv_url: str) -> Tuple[Set[Tuple[int, int, int]], List[CancelledEvent]]:
    """通过优化的HTML访问参数检测删除线样式的日期和事件"""
    cancelled_dates: Set[Tuple[int, int, int]] = set()
    cancelled_events: List[CancelledEvent] = []
    
    logger.info("🔍 正在尝试HTML删除线检测...")
    
    try:
        # 构建能获取完整样式的HTML URL (关键：widget=false)
        base_url = csv_url.replace('/pub?', '/pubhtml?').replace('&output=csv', '')
        
        # 使用发现的有效参数组合
        html_urls = [
            base_url + '&widget=false',  # 关键参数！能获取完整样式
            base_url,  # 备用：基础URL
        ]
        
        for i, html_url in enumerate(html_urls):
            try:
                logger.debug(f"尝试URL变体 {i+1}: ...{html_url[-50:]}")
                response = requests.get(html_url, headers=Config.HEADERS, timeout=Config.REQUEST_TIMEOUT)
                response.raise_for_status()
                
                # 检查响应是否包含样式信息
                html_content = response.text
                if any(pattern for pattern in Config.STRIKETHROUGH_PATTERNS 
                      if re.search(pattern, html_content)):
                    logger.info("✓ 找到包含样式的HTML内容!")
                    cancelled_dates, cancelled_events = parse_html_for_strikethrough(html_content)
                    if cancelled_dates or cancelled_events:
                        return cancelled_dates, cancelled_events
                else:
                    logger.debug("该URL未包含完整样式信息")
                    
            except requests.RequestException as e:
                logger.warning(f"URL变体 {i+1} 请求失败: {e}")
                continue
            except Exception as e:
                logger.warning(f"URL变体 {i+1} 处理失败: {e}")
                continue
        
        logger.info("所有HTML访问尝试均未成功获取样式信息")
        
    except Exception as e:
        logger.error(f"HTML检测整体失败: {e}")
    
    return cancelled_dates, cancelled_events

def parse_html_for_strikethrough(html_source: Union[str, os.PathLike]) -> Tuple[Set[Tuple[int, int, int]], List[CancelledEvent]]:
    """
    解析HTML内容查找删除线样式的日期和具体课程内容
    
    Args:
        html_source: HTML内容字符串或HTML文件路径
        
    Returns:
        Tuple[Set, List]: (完全取消的日期集合, 部分取消的事件列表)
    """
    cancelled_dates = set()
    cancelled_events = []
    
    try:
        # 智能检测输入类型：文件路径还是HTML内容
        if isinstance(html_source, (str, os.PathLike)) and len(str(html_source)) < 300 and str(html_source).endswith('.html'):
            with open(html_source, 'r', encoding='utf-8') as f:
                html_content = f.read()
        else:
            html_content = str(html_source)
            
        soup = BeautifulSoup(html_content, 'html.parser')
        
        # 方法1: 查找CSS类级别的删除线样式（整个单元格取消）
        strikethrough_classes = set()
        style_tags = soup.find_all('style')
        
        for style_tag in style_tags:
            css_content = style_tag.string or ''
            # 使用配置中的模式
            for pattern in Config.STRIKETHROUGH_PATTERNS:
                css_rules = re.findall(rf'\.([a-zA-Z0-9_-]+)\{{[^}}]*{pattern}[^}}]*\}}', css_content)
                strikethrough_classes.update(css_rules)
                if css_rules:
                    logger.info(f"✓ 找到CSS删除线样式类: {css_rules}")
        
        # 处理CSS类级别的删除线
        if strikethrough_classes:
            for class_name in strikethrough_classes:
                cells = soup.find_all('td', class_=lambda x: x and class_name in x.split())
                logger.info(f"找到 {len(cells)} 个使用 {class_name} 类的单元格")
                
                for cell in cells:
                    current_row = cell.find_parent('tr')
                    if current_row:
                        current_cells = current_row.find_all('td')
                        cell_position = -1
                        for j, curr_cell in enumerate(current_cells):
                            if curr_cell == cell:
                                cell_position = j
                                break
                        
                        prev_row = current_row.find_previous_sibling('tr')
                        if prev_row and cell_position >= 0:
                            prev_cells = prev_row.find_all('td')
                            if cell_position < len(prev_cells):
                                date_cell = prev_cells[cell_position]
                                date_text = date_cell.get_text(strip=True)
                                
                                date_matches = re.findall(r'\b(\d{1,2})/(\d{1,2})\b', date_text)
                                for day_str, month_str in date_matches:
                                    day, month = int(day_str), int(month_str)
                                    # 1月到7月属于2026年（春季学期），8月到12月属于2025年（秋季学期）
                                    year = 2026 if 1 <= month <= 7 else 2025
                                    cancelled_dates.add((year, month, day))
                                    logger.info(f"🚫 检测到整日课程取消: {day:02d}/{month:02d}/{year}")
        
        # 方法2: 查找内联样式的删除线（span级别的部分取消）
        inline_strikethrough_spans = soup.find_all('span', 
            style=lambda x: x and any(re.search(pattern, x) for pattern in Config.STRIKETHROUGH_PATTERNS))
        logger.info(f"找到 {len(inline_strikethrough_spans)} 个内联删除线span元素")
        
        for span in inline_strikethrough_spans:
            parent_cell = span.find_parent('td')
            if parent_cell:
                current_row = parent_cell.find_parent('tr')
                if current_row:
                    current_cells = current_row.find_all('td')
                    cell_position = -1
                    for j, curr_cell in enumerate(current_cells):
                        if curr_cell == parent_cell:
                            cell_position = j
                            break
                    
                    prev_row = current_row.find_previous_sibling('tr')
                    if prev_row and cell_position >= 0:
                        prev_cells = prev_row.find_all('td')
                        if cell_position < len(prev_cells):
                            date_cell = prev_cells[cell_position]
                            date_text = date_cell.get_text(strip=True)
                            
                            date_matches = re.findall(r'\b(\d{1,2})/(\d{1,2})\b', date_text)
                            for day_str, month_str in date_matches:
                                day, month = int(day_str), int(month_str)
                                # 1月到7月属于2026年（春季学期），8月到12月属于2025年（秋季学期）
                                year = 2026 if 1 <= month <= 7 else 2025
                                
                                cancelled_content = span.get_text(strip=True)
                                cancelled_events.append(CancelledEvent(
                                    date=(year, month, day),
                                    content=cancelled_content,
                                    event_type='partial'
                                ))
                                logger.info(f"🚫 检测到部分课程取消: {day:02d}/{month:02d}/{year}")
                                logger.info(f"    取消内容: {cancelled_content}")
        
    except Exception as e:
        logger.error(f"HTML解析失败: {e}")
    
    return cancelled_dates, cancelled_events

def parse_time(time_str: str) -> Optional[ParsedTime]:
    """解析时间字符串，返回ParsedTime对象"""
    # 统一格式：将 : 替换为 h，方便处理
    time_str = time_str.lower().replace(':', 'h')
    
    if 'h' in time_str:
        parts = time_str.split('h')
        try:
            hour = int(parts[0])
            minute = int(parts[1]) if len(parts) > 1 and parts[1] else 0
            return ParsedTime(hour=hour, minute=minute)
        except (ValueError, IndexError):
            logger.warning(f"无法解析时间字符串: {time_str}")
    return None

def parse_cell_content(cell_text: str, year: int, month: int, day: int, 
                      cancelled_events: Optional[List[CancelledEvent]] = None) -> List[Event]:
    """解析单元格内容，提取课程事件，并排查部分取消的事件"""
    events: List[Event] = []
    if not cell_text.strip():
        return events
    
    # 检查是否有部分取消的事件
    cancelled_content_list = []
    if cancelled_events:
        for cancelled_event in cancelled_events:
            if cancelled_event.date == (year, month, day):
                cancelled_content_list.append(cancelled_event.content.strip())
    
    logger.debug(f"日期 {day:02d}/{month:02d}/{year} 的取消内容: {cancelled_content_list}")
    
    # 按空行分割不同的事件块
    event_blocks = re.split(r'\n\s*\n', cell_text.strip())
    
    for block in event_blocks:
        lines = [line.strip() for line in block.split('\n') if line.strip()]
        if not lines:
            continue
            
        # 查找所有时间信息（改进：找出所有时间段，而不只是第一个）
        time_events = []
        main_instructor = ""
        global_description_lines = []
        
        # 首先收集所有时间匹配和基本信息
        for i, line in enumerate(lines):
            # 匹配时间格式：9h-12h, 14h30-17h30, 18h-21h 等
            time_matches = re.finditer(Config.TIME_PATTERN, line)
            for time_match in time_matches:
                start_str, end_str = time_match.groups()
                
                # 提取课程名称（通常在时间后面）
                time_end = time_match.end()
                remaining_text = line[time_end:].strip()
                if remaining_text.startswith(':'):
                    remaining_text = remaining_text[1:].strip()
                
                event_name = remaining_text if remaining_text else "Événement"
                
                time_events.append({
                    'line': line,
                    'start_str': start_str,
                    'end_str': end_str,
                    'event_name': event_name,
                    'original_line_index': i
                })
            
            # 查找教师名称（通常是姓名格式）
            line_text = line.strip()
            if re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+', line_text) or 'Mohammed' in line_text or 'Marie' in line_text:
                if not main_instructor:  # 只取第一个找到的教师名称作为主教师
                    main_instructor = line_text
            elif not any(Config.TIME_PATTERN in line for pattern in [Config.TIME_PATTERN] if re.search(pattern, line)):
                # 如果这行不包含时间信息，可能是描述
                global_description_lines.append(line_text)
        
        # 如果没有找到任何时间事件，创建一个默认事件
        if not time_events:
            time_line = None
            event_name = "Événement"
            instructor = ""
            description_lines = []
            
            for i, line in enumerate(lines):
                # 匹配时间格式：9h-12h, 14h30-17h30, 18h-21h 等
                time_match = re.search(Config.TIME_PATTERN, line)
                if time_match:
                    time_line = line
                    start_str, end_str = time_match.groups()
                    
                    # 提取课程名称（通常在时间后面）
                    time_end = time_match.end()
                    remaining_text = line[time_end:].strip()
                    if remaining_text.startswith(':'):
                        remaining_text = remaining_text[1:].strip()
                    
                    if remaining_text:
                        event_name = remaining_text
                    elif i + 1 < len(lines):
                        event_name = lines[i + 1]
                    
                    # 查找教师名称（通常是最后一行或特定格式）
                    for j in range(i + 1, len(lines)):
                        line_text = lines[j].strip()
                        # 教师名称通常是姓名格式
                        if re.match(r'^[A-Z][a-z]+\s+[A-Z][a-z]+', line_text) or 'Mohammed' in line_text or 'Marie' in line_text:
                            instructor = line_text
                        else:
                            description_lines.append(line_text)
                    
                    break
        
        # 处理找到的时间事件
        if time_events:
            # 为每个时间段创建独立的事件
            for time_event in time_events:
                time_line = time_event['line']
                start_str = time_event['start_str']
                end_str = time_event['end_str']
                event_name = time_event['event_name']
                
                # 检查这个事件是否被部分取消
                event_cancelled = False
                for cancelled_content in cancelled_content_list:
                    if cancelled_content and (
                        cancelled_content in block or 
                        any(cancelled_content in line for line in lines) or
                        f"{start_str}-{end_str}" in cancelled_content
                    ):
                        logger.info(f"⚠️  跳过被取消的事件: {time_line[:50]}...")
                        event_cancelled = True
                        break
                
                if event_cancelled:
                    continue
                    
                try:
                    start_time = parse_time(start_str)
                    end_time = parse_time(end_str)
                    
                    if start_time and end_time:
                        event = Event()
                        # 使用巴黎时区
                        paris_tz = ZoneInfo(Config.TIMEZONE)
                        event.begin = datetime(year, month, day, start_time.hour, start_time.minute, tzinfo=paris_tz)
                        event.end = datetime(year, month, day, end_time.hour, end_time.minute, tzinfo=paris_tz)
                        
                        # 构建事件标题
                        final_event_name = event_name
                        
                        # 如果事件名称是默认值，且有全局描述行，尝试使用第一行描述作为标题
                        if final_event_name == "Événement" and global_description_lines:
                            final_event_name = global_description_lines[0]
                        
                        if main_instructor:
                            event.name = f"{final_event_name} - {main_instructor}"
                        else:
                            event.name = final_event_name
                        
                        # 构建描述
                        desc_parts = []
                        if main_instructor and main_instructor not in event_name:
                            desc_parts.append(f"教师: {main_instructor}")
                        
                        # 添加其他时间段信息到描述中（如果有多个时间段）
                        if len(time_events) > 1:
                            other_times = [f"{te['start_str']}-{te['end_str']} : {te['event_name']}" 
                                         for te in time_events if te != time_event]
                            if other_times:
                                desc_parts.extend(other_times)
                        
                        if global_description_lines:
                            desc_parts.extend(global_description_lines)
                        
                        event.description = "\n".join(desc_parts) if desc_parts else ""
                        
                        # 添加时间戳 (Phase 1: 标准必需属性)
                        event.created = datetime.now(tz=paris_tz)
                        event.last_modified = datetime.now(tz=paris_tz)
                        
                        events.append(event)
                        
                except (ValueError, IndexError) as e:
                    logger.warning(f"解析事件时出错 '{time_line}': {e}")
        
        elif time_line:
            # 处理旧的单时间事件逻辑（作为备用）
            # 检查这个事件是否被部分取消
            event_cancelled = False
            for cancelled_content in cancelled_content_list:
                if cancelled_content and (
                    cancelled_content in block or 
                    any(cancelled_content in line for line in lines) or
                    (start_str and end_str and f"{start_str}-{end_str}" in cancelled_content)
                ):
                    logger.info(f"⚠️  跳过被取消的事件: {time_line[:50]}...")
                    event_cancelled = True
                    break
            
            if event_cancelled:
                continue
                
            try:
                time_match = re.search(Config.TIME_PATTERN, time_line)
                start_str, end_str = time_match.groups()
                
                start_time = parse_time(start_str)
                end_time = parse_time(end_str)
                
                if start_time and end_time:
                    event = Event()
                    # 使用巴黎时区
                    paris_tz = ZoneInfo(Config.TIMEZONE)
                    event.begin = datetime(year, month, day, start_time.hour, start_time.minute, tzinfo=paris_tz)
                    event.end = datetime(year, month, day, end_time.hour, end_time.minute, tzinfo=paris_tz)
                    
                    # 构建事件标题
                    if instructor:
                        event.name = f"{event_name} - {instructor}"
                    else:
                        event.name = event_name
                    
                    # 构建描述
                    desc_parts = []
                    if instructor and instructor not in event_name:
                        desc_parts.append(f"教师: {instructor}")
                    if description_lines:
                        desc_parts.extend(description_lines)
                    
                    event.description = "\n".join(desc_parts) if desc_parts else ""
                    
                    # 添加时间戳 (Phase 1: 标准必需属性)
                    paris_tz = ZoneInfo(Config.TIMEZONE)
                    event.created = datetime.now(tz=paris_tz)
                    event.last_modified = datetime.now(tz=paris_tz)
                    
                    events.append(event)
                    
            except (ValueError, IndexError) as e:
                logger.warning(f"解析事件时出错 '{time_line}': {e}")
        
        else:
            # 处理没有时间信息的事件块 (例如 "Projets collaboratifs")
            # 检查这个事件是否被取消
            event_cancelled = False
            for cancelled_content in cancelled_content_list:
                if cancelled_content and cancelled_content in block:
                    logger.info(f"⚠️  跳过被取消的事件: {block[:50]}...")
                    event_cancelled = True
                    break
            
            if not event_cancelled and block.strip():
                # 创建全天事件
                event = Event()
                # 使用naive datetime避免时区转换导致日期偏移 (例如 00:00 Paris -> 23:00 UTC 前一天)
                event.begin = datetime(year, month, day)
                event.make_all_day()
                
                # 使用第一行作为标题
                lines = block.strip().split('\n')
                event.name = lines[0].strip()
                
                # 其余作为描述
                if len(lines) > 1:
                    event.description = "\n".join(line.strip() for line in lines[1:])
                
                # 添加时间戳
                paris_tz = ZoneInfo(Config.TIMEZONE)
                event.created = datetime.now(tz=paris_tz)
                event.last_modified = datetime.now(tz=paris_tz)
                
                events.append(event)
    
    return events


class ScheduleProcessor:
    """课程计划处理器"""
    
    def __init__(self):
        self.calendar = Calendar()
        self.config_cancelled_dates = load_cancelled_dates()
        self._setup_calendar_properties()
    
    def _setup_calendar_properties(self):
        """设置日历的Webcal订阅属性"""
        # Phase 1: 安全的标准属性
        self.calendar.method = Config.CALENDAR_METHOD
        
        # 不在这里设置extra属性，避免序列化问题
        # 所有特殊属性都在保存时通过_inject_webcal_properties添加
        
        logger.info(f"📡 配置Webcal订阅: 每{Config.REFRESH_INTERVAL_HOURS}小时更新")
        
    def process_schedule(self) -> bool:
        """
        处理课程计划，生成ICS文件
        
        Returns:
            bool: 成功返回True，失败返回False
        """
        with timer("整个处理过程"):
            logger.info("📅 开始生成课程日历 (V5.1 - 深度优化版本)...")
            
            # 动态获取月份和URL
            with timer("提取月份URL"):
                month_urls = extract_month_urls()
                if not month_urls:
                    logger.error("无法获取月份信息，程序退出")
                    return False
            
            # 处理每个月份的数据
            for month_name, csv_url in month_urls.items():
                with timer(f"处理 {month_name} 数据"):
                    if not self._process_month_data(month_name, csv_url):
                        logger.warning(f"处理 {month_name} 数据失败，跳过...")
                        continue
            
            # 生成最终的ICS文件
            with timer("保存ICS文件"):
                return self._save_calendar()
    
    def _process_month_data(self, month_name: str, csv_url: str) -> bool:
        """处理单个月份的数据"""
        logger.info(f"正在处理 {month_name.capitalize()} 的数据...")
        
        try:
            # HTML删除线检测
            html_cancelled_dates, html_cancelled_events = detect_strikethrough_from_html(csv_url)
            all_cancelled_dates = self.config_cancelled_dates.union(html_cancelled_dates)
            
            # 下载CSV数据
            csv_data = self._download_csv_data(csv_url)
            if not csv_data:
                return False
                
            # 解析CSV数据并添加事件
            self._parse_csv_data(csv_data, all_cancelled_dates, html_cancelled_dates, html_cancelled_events)
            return True
            
        except Exception as e:
            logger.error(f"处理 {month_name} 数据时发生错误: {e}")
            return False
    
    def _download_csv_data(self, csv_url: str) -> Optional[List[List[str]]]:
        """下载并解析CSV数据"""
        try:
            response = requests.get(csv_url, headers=Config.HEADERS, timeout=Config.REQUEST_TIMEOUT)
            response.raise_for_status()
            
            csv_data_string = response.content.decode('utf-8')
            csv_file = StringIO(csv_data_string)
            reader = csv.reader(csv_file)
            data = list(reader)
            
            if not data or len(data) < 2:
                logger.warning("CSV数据为空或格式不正确")
                return None
                
            return data
            
        except requests.RequestException as e:
            logger.error(f"下载CSV数据失败: {e}")
            return None
        except UnicodeDecodeError as e:
            logger.error(f"CSV编码解析失败: {e}")
            return None
        except Exception as e:
            logger.error(f"解析CSV数据失败: {e}")
            return None
    
    def _parse_csv_data(self, data: List[List[str]], all_cancelled_dates: Set[Tuple[int, int, int]], 
                       html_cancelled_dates: Set[Tuple[int, int, int]], 
                       html_cancelled_events: List[CancelledEvent]) -> None:
        """解析CSV数据并添加事件到日历"""
        i = 0
        while i < len(data):
            row = data[i]
            
            # 检查当前行是否包含日期
            if self._row_contains_date(row):
                date_row = row
                
                # 处理下一行的课程内容
                if i + 1 < len(data):
                    content_row = data[i + 1]
                    self._process_date_content_pair(
                        date_row, content_row, all_cancelled_dates, 
                        html_cancelled_dates, html_cancelled_events
                    )
                
                i += 2  # 跳过内容行
            else:
                i += 1
    
    def _row_contains_date(self, row: List[str]) -> bool:
        """检查行是否包含日期"""
        return any(re.search(r'\d{1,2}/\d{1,2}', cell) for cell in row)
    
    def _process_date_content_pair(self, date_row: List[str], content_row: List[str],
                                  all_cancelled_dates: Set[Tuple[int, int, int]],
                                  html_cancelled_dates: Set[Tuple[int, int, int]],
                                  html_cancelled_events: List[CancelledEvent]) -> None:
        """处理日期行和内容行的配对"""
        for date_cell, content_cell in zip(date_row, content_row):
            if not content_cell.strip():
                continue
                
            parsed_date = self._extract_date_from_cell(date_cell)
            if not parsed_date:
                continue
                
            year, month, day = parsed_date
            
            # 检查是否被取消
            if (year, month, day) in all_cancelled_dates:
                source = "HTML删除线检测" if (year, month, day) in html_cancelled_dates else "配置文件"
                logger.info(f"跳过 {day:02d}/{month:02d}/{year} 的课程 ({source})")
                continue
            
            logger.info(f"处理 {day:02d}/{month:02d}/{year} 的课程...")
            
            # 解析单元格内容并添加事件
            events = parse_cell_content(content_cell, year, month, day, html_cancelled_events)
            for event in events:
                self.calendar.events.add(event)
                logger.info(f"添加事件: {event.name}")
    
    def _extract_date_from_cell(self, cell: str) -> Optional[Tuple[int, int, int]]:
        """从单元格提取日期信息"""
        date_match = re.search(r'(\d{1,2})/(\d{1,2})', cell)
        if date_match:
            day, month = int(date_match.group(1)), int(date_match.group(2))
            # 1月到7月属于2026年（春季学期），8月到12月属于2025年（秋季学期）
            year = 2026 if 1 <= month <= 7 else 2025
            return (year, month, day)
        return None
    
    def _save_calendar(self) -> bool:
        """保存日历到文件，包含Webcal订阅属性"""
        try:
            # 获取原始的ICS内容（使用serialize方法避免clone错误）
            try:
                # 推荐方法：使用serialize()
                ics_content = self.calendar.serialize()
            except AttributeError:
                # 备用方法：使用serialize_iter()
                try:
                    ics_content = ''.join(self.calendar.serialize_iter())
                except AttributeError:
                    # 最后备用：使用str()但会有警告
                    import warnings
                    with warnings.catch_warnings():
                        warnings.simplefilter("ignore")
                        ics_content = str(self.calendar)
            
            # 手动注入Webcal订阅属性
            ics_content = self._inject_webcal_properties(ics_content)
            
            with open(Config.OUTPUT_FILENAME, 'w', encoding='utf-8') as f:
                f.write(ics_content)
            
            logger.info(f"🎉 成功! 日历文件已生成: {os.path.abspath(Config.OUTPUT_FILENAME)}")
            logger.info("请将此文件导入您的iPhone日历。")
            logger.info(f"总共生成了 {len(self.calendar.events)} 个事件")
            
            # 显示Webcal配置信息
            if Config.ENABLE_REFRESH_INTERVAL:
                logger.info(f"📡 已配置Webcal订阅: 每{Config.REFRESH_INTERVAL_HOURS}小时更新")
            
            return True
            
        except PermissionError:
            logger.error(f"没有权限写入文件: {Config.OUTPUT_FILENAME}")
            return False
        except Exception as e:
            logger.error(f"保存日历文件失败: {e}")
            return False
    
    def _inject_webcal_properties(self, ics_content: str) -> str:
        """在ICS内容中注入Webcal订阅属性"""
        try:
            lines = ics_content.split('\n')
            injected_lines = []
            
            for line in lines:
                injected_lines.append(line)
                
                # 在VCALENDAR开始后立即添加属性
                if line.strip() == 'BEGIN:VCALENDAR':
                    # Phase 2: 标准更新控制
                    if Config.ENABLE_REFRESH_INTERVAL:
                        refresh_interval = f"PT{Config.REFRESH_INTERVAL_HOURS}H"
                        injected_lines.append(f"REFRESH-INTERVAL:{refresh_interval}")
                    
                    # Phase 3: 扩展属性
                    if Config.PUBLISH_TTL_HOURS:
                        ttl_interval = f"PT{Config.PUBLISH_TTL_HOURS}H"
                        injected_lines.append(f"X-PUBLISHED-TTL:{ttl_interval}")
                    
                    # 添加日历描述信息
                    injected_lines.append(f"X-WR-CALDESC:MIASHS课程计划 - 每{Config.REFRESH_INTERVAL_HOURS}小时更新")
                    injected_lines.append(f"X-WR-CALNAME:MIASHS Master Handicap Schedule")
            
            return '\n'.join(injected_lines)
            
        except Exception as e:
            logger.error(f"注入Webcal属性失败: {e}")
            # 返回原始内容，至少保证基本功能
            return ics_content


# 保持兼容性的独立函数
def parse_schedule_from_dynamic_urls():
    """旧版本兼容函数"""
    main()
def main() -> None:
    """主函数"""
    try:
        processor = ScheduleProcessor()
        success = processor.process_schedule()
        if not success:
            logger.error("程序执行失败")
            exit(1)
        else:
            logger.info("程序执行成功完成!")
    except KeyboardInterrupt:
        logger.info("程序被用户中断")
        exit(0)
    except Exception as e:
        logger.error(f"程序发生未预期错误: {e}")
        exit(1)


if __name__ == "__main__":
    main()