FROM python:3.11-slim
RUN apt-get update && apt-get install -y ffmpeg && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .

# As fixtures da vitrine nascem AQUI, no build: são artefato (29 MB) e ficam
# fora do repositório. Gerar no build deixa a imagem autossuficiente — sem isto,
# um restart do container acharia `seed/fixtures` vazio e todas as telas
# abririam em branco até alguém rodar o seed à mão.
RUN python -X utf8 seed/gerar.py

EXPOSE 5000
CMD ["python", "server.py"]
