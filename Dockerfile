FROM python:3.11-slim

LABEL maintainer="easyzoom"
LABEL description="A股股票筛选工具"

# 设置工作目录
WORKDIR /app

# 设置 pip 使用清华源加速
RUN pip config set global.index-url https://pypi.tuna.tsinghua.edu.cn/simple

# 先复制依赖文件，利用 Docker 缓存层
COPY requirements.txt .

# 安装依赖
RUN pip install --no-cache-dir -r requirements.txt

# 复制项目文件
COPY . .

# 创建输出和缓存目录（确保挂载时存在）
RUN mkdir -p /app/output /app/cache

# 声明数据卷（方便外部挂载 output 和 cache）
VOLUME ["/app/output", "/app/cache"]

# 默认运行主程序
CMD ["python", "main.py"]
