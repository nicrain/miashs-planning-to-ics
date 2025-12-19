# 📅 课程计划解析器 V5.2

> 自动从Google Sheets生成ICS日历文件，支持智能删除线检测和多时间段事件解析

## 🎯 功能特性

### 核心功能
- **动态URL提取**: 自动从主页面获取所有月份的Google Sheets链接
- **智能删除线检测**: 支持CSS类和内联样式两种删除线检测方式
- **多时间段事件解析**: 自动识别并创建同一单元格中多个时间段的独立事件
- **多层次取消检测**: 区分整日取消和部分课程取消
- **配置文件支持**: 手动维护额外的取消日期
- **完整时区支持**: 使用巴黎时区处理所有时间

### 检测能力
- ✅ **CSS类删除线** - 检测如`.s15`等样式类的整日取消
- ✅ **内联span删除线** - 检测单个课程的部分取消
- ✅ **多时间段解析** - 自动识别如"11h-13h : LSF niveau 1"和"15h-17h : LSF niveau 3"的独立事件
- ✅ **配置文件取消** - 支持手动添加取消日期
- ✅ **智能URL参数** - 自动添加`&widget=false`获取完整样式

## 🚀 使用方法

### 基本使用
```bash
python3 planning.py
```

### 配置取消日期
创建 `cancelled_dates.txt` 文件：
```
# 手动取消的日期 (格式: DD/MM 或 DD/MM/YYYY)
02/10/2025
15/11
# 注释行以#开头
```

## 📊 输出示例

```
INFO: 📅 开始生成课程日历 (V5 - 优化版本)...
INFO: 正在从主页面提取月份信息...
INFO: 找到: septembre -> https://docs.google.com/spreadsheets/...
INFO: 正在处理 Septembre 的数据...
   🔍 正在尝试HTML删除线检测...
     ✓ 找到包含样式的HTML内容!
     🚫 检测到部分课程取消: 24/09/2025
         取消内容: 18h-21h : Gestion de projet Mohammed Zbakh (1)
INFO: 处理 09/10/2025 的课程...
INFO: 添加事件: Arduino  (6) - Salvatore Anzalone
INFO: 添加事件: LSF niveau 1 - Marie Josée Henriquet
INFO: 添加事件: LSF niveau 3 - Marie Josée Henriquet  ← 自动创建独立事件
INFO: 添加事件: Conférence
INFO: 🎉 成功! 日历文件已生成: master_handi_schedule.ics
```

## 🏗️ 架构设计

### 类结构
```python
class Config:
    """集中管理所有配置常量"""
    PLANNING_URL = "https://handiman.univ-paris8.fr/..."
    SUPPORTED_MONTHS = {'septembre', 'octobre', ...}
    HEADERS = {...}

@dataclass
class CancelledEvent:
    """取消事件的数据结构"""
    date: Tuple[int, int, int]
    content: str
    event_type: str

class ScheduleProcessor:
    """主要的处理器类"""
    def process_schedule(self) -> bool
    def _process_month_data(self, month_name, csv_url) -> bool
    def _download_csv_data(self, csv_url) -> Optional[List[List[str]]]
```

### 处理流程
1. **提取月份URL** - 从主页面动态获取所有月份链接
2. **HTML删除线检测** - 分析CSS和内联样式找出取消的课程
3. **CSV数据下载** - 获取实际的课程数据
4. **事件解析与过滤** - 解析课程内容并跳过取消的事件
5. **ICS文件生成** - 生成标准日历格式文件

## 📈 版本历史

### V5.2 (2025-12-19) - 春季学期支持
- 🎯 **新功能**: 添加对春季学期月份（3月-7月）的识别支持
- 🔧 **修复**: 解决了月份链接数量不匹配导致的程序退出问题
- 📈 **范围**: 支持完整的学年课程计划解析

### V5.1 (2025-10-06) - 多时间段事件解析
- 🎯 **新功能**: 支持同一单元格中多个时间段的独立事件创建
- 🔧 **修复**: 解决了LSF niveau 3等课程被合并到其他事件描述中的问题
- 📈 **改进**: 事件总数从132个增加到148个，提高日历完整性
- 💡 **示例**: "11h-13h : LSF niveau 1" + "15h-17h : LSF niveau 3" 现在创建两个独立事件

### V5.0 (2025-10-06) - 重大架构升级
- 🎯 **新功能**: HTML删除线自动检测
- 🏗️ **架构**: 面向对象重构，引入类型注解
- 🔧 **优化**: 标准化日志，错误处理改进
- 📊 **检测**: 支持CSS类和span内联两种删除线

### V4.0 - 动态URL支持
- 自动从主页面提取月份链接
- 支持配置文件手动取消

### V3.0 - 多月份支持
- 扩展到6个月课程计划
- 时区处理优化

### V2.0 - 基础解析功能
- 基本的CSV解析和ICS生成
- 时间格式处理

### V1.0 - 概念验证
- 单月份硬编码处理

## 🔍 检测示例

### CSS类删除线检测
```css
.s15 {
    text-decoration: line-through;
    background-color: #e69138;
}
```
→ 检测到整日取消: 02/10/2025

### 内联span删除线检测
```html
<span style="text-decoration:line-through;">
18h-21h : Gestion de projet<br/>Mohammed Zbakh (1)
</span>
```
→ 检测到部分取消: 24/09/2025的18h-21h课程

### 多时间段事件解析
```
单元格内容:
11h-13h : LSF niveau 1
15h-17h : LSF niveau 3
Marie Josée Henriquet
```
→ 自动创建两个独立事件:
- 11:00-13:00: LSF niveau 1 - Marie Josée Henriquet
- 15:00-17:00: LSF niveau 3 - Marie Josée Henriquet

## 📦 依赖项

```python
requests>=2.25.0
beautifulsoup4>=4.9.0
ics>=0.7.0
```

## 🛠️ 开发

### 环境设置
```bash
# 激活虚拟环境
source ../../.venv/bin/activate

# 安装依赖
pip install requests beautifulsoup4 ics

# 运行
python3 planning.py
```

### 调试模式
```python
import logging
logging.basicConfig(level=logging.DEBUG)
```

## 📝 许可证

MIT License - 自由使用和修改

## 👨‍💻 贡献者

- **主要开发**: GitHub Copilot Assistant
- **架构设计**: 面向对象 + 类型安全
- **测试验证**: 实际课程数据验证

---

**生成的文件**: `master_handi_schedule.ics` (可直接导入iPhone/Google日历)