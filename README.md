<h1 align="center">A股股票筛选工具</h1>

<p align="center">
  一个基于 Python 的 A 股主板股票筛选与行情分析工程
</p>

<p align="center">
  <img src="https://img.shields.io/github/stars/Sust2014/stock_selection?style=social" />
  <img src="https://img.shields.io/github/forks/Sust2014/stock_selection?style=social" />
  <img src="https://img.shields.io/github/issues/Sust2014/stock_selection" />
  <img src="https://img.shields.io/github/license/Sust2014/stock_selection" />
</p>

---

## ⚠️ 免责声明

本项目仅用于个人学习、数据分析和量化策略研究，不构成任何投资建议。股市有风险，投资需谨慎。

---

## 🌟 功能特性

- ✅ 筛选 A 股主板股票
- ✅ 排除创业板、科创板、北交所、北交所、ST 股票
- ✅ 支持按市值区间筛选
- ✅ 支持排除指定行业
- ✅ 支持历史 K 线数据获取
- ✅ 支持本地缓存，减少重复请求
- ✅ 支持多种技术形态选股策略
- ✅ 可扩展技术指标和策略条件

---

## 📌 筛选条件与选股策略

本项目先进行基础股票池筛选，再基于日 K 线数据执行多种技术形态策略，用于辅助盘后选股分析。

---

### 一、基础筛选条件

当前默认股票池筛选规则如下：

| 条件 | 说明 |
|---|---|
| 市场范围 | A 股主板 |
| 排除板块 | 创业板、科创板、北交所 |
| 排除股票 | ST、*ST |
| 市值范围 | 100 亿至 1500 亿 |
| 排除行业 | 银行、券商、保险、信托、房地产、钢铁、煤炭开采、铁路运输、航运 |

---

### 二、选股策略

当前内置以下技术形态选股策略：

| 策略名称 | 核心逻辑 | 筛选条件 |
|---|---|---|
| 箱体突破 | 前期横盘后放量创新高 | 今日收盘价高于过去 60 个交易日最高价；今日成交量大于过去 20 日均量的 1.3 倍；过去 20 个交易日 K 线实体振幅不超过 20% |
| 底部放量反转 | 低位 V 型启动 | 当前价格距离过去 40 个交易日最低点小于 20%；今日涨幅大于 5%；今日成交量大于过去 20 日均量的 2 倍 |
| 缩量回调启动 | 短期回调结束后重新启动 | SMA(5) < SMA(20)；SMA(60) 大于 5 个交易日前的 SMA(60)；今日收盘价高于 SMA(5)；今日成交量大于过去 20 日均量的 1.5 倍 |
| 均线多头排列 | 趋势延续型启动 | SMA(5) > SMA(10) > SMA(20) > SMA(60)；今日涨幅大于 2%；今日成交量大于过去 20 日均量的 1.2 倍 |

---

### 三、策略说明

#### 1. 箱体突破

该策略用于寻找前期长期横盘整理后，突然放量突破前高的股票。

筛选逻辑：

```text
1. 今日收盘价 > 过去 60 个交易日最高价，不含今日
2. 今日成交量 > 过去 20 日均量 × 1.3
3. 过去 20 个交易日 K 线实体振幅 <= 20%
```

其中，箱体振幅使用每日 `open` 和 `close` 计算实体上下沿，避免长上影线或长下影线造成误判。

---

#### 2. 底部放量反转

该策略用于寻找低位区域出现明显放量上涨的股票，偏向 V 型反转启动形态。

筛选逻辑：

```text
1. 当前价格距离过去 40 个交易日最低点 < 20%
2. 今日涨幅 > 5%
3. 今日成交量 > 过去 20 日均量 × 2
```

---

#### 3. 缩量回调启动

该策略用于寻找中长期趋势未破坏，但短期回调后重新启动的股票。

筛选逻辑：

```text
1. SMA(5) < SMA(20)
2. SMA(60) > 5 个交易日前的 SMA(60)
3. 今日收盘价 > SMA(5)
4. 今日成交量 > 过去 20 日均量 × 1.5
```

其中，`SMA(5) < SMA(20)` 表示短期仍处于回调状态，`SMA(60)` 上行表示长期趋势没有明显走坏，今日重新站上 `SMA(5)` 则代表短期可能重新启动。

---

#### 4. 均线多头排列

该策略用于寻找趋势较强、均线系统呈现多头排列的股票。

筛选逻辑：

```text
1. SMA(5) > SMA(10) > SMA(20) > SMA(60)
2. 今日涨幅 > 2%
3. 今日成交量 > 过去 20 日均量 × 1.2
```

---

## 🧰 技术栈

| 类型 | 工具 |
|---|---|
| 开发语言 | Python |
| 数据处理 | pandas |
| 行情数据 | BaoStock / AkShare |
| 表格输出 | tabulate / wcwidth |
| Excel 支持 | openpyxl |
| 数据缓存 | CSV |
| 运行环境 | Windows / Linux / macOS |

---

## 📦 安装依赖

请先确保已经安装 Python，建议使用 Python 3.9 及以上版本。

项目依赖如下：

```txt
akshare
pandas
openpyxl
tabulate
baostock
wcwidth
```

推荐使用 `requirements.txt` 安装：

```bash
pip install -r requirements.txt
```

Windows 环境也可以使用：

```bash
python -m pip install -r requirements.txt
```

如果下载速度较慢，可以使用清华源：

```bash
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

---

## 🚀 快速开始

```bash
git clone git@github.com:Sust2014/stock_selection.git

cd stock_selection

python main.py
```

---

## 📁 项目结构

```text
stock_selection/
├── main.py
├── requirements.txt
├── cache/
├── output/
├── test/
└── README.md
```

---

## 🔧 后续计划

- [ ] 增加成交量筛选
- [ ] 增加均线趋势判断
- [ ] 增加 MACD 金叉判断
- [ ] 增加布林线突破判断
- [ ] 增加行业热度分析
- [ ] 增加代理池或数据源自动切换机制
- [ ] 增加图形化界面

---

## 📄 License

本项目基于 GPL-3.0 license 开源。