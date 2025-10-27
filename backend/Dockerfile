FROM python:3.11-slim

# 2.2 Set workdir
WORKDIR /backend

# 2.3 Install dependencies
COPY backend/requirements.txt ./requirements.txt
RUN python -m pip install --upgrade pip==24.0 setuptools wheel
RUN pip install --no-cache-dir -r requirements.txt

# 2.4 Copy application code
COPY backend/ .

CMD ["bash"]