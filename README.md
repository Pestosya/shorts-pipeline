# 🎬 Shorts Pipeline

Производственный пайплайн для автоматизации создания коротких видео.

## 📋 Содержание
1. [Системные требования](#-системные-требования)
2. [Быстрая установка](#-быстрая-установка)
3. [Авторизация в Youtube](#3-авторизация-в-youtube)
4. [Детальная настройка](#-детальная-настройка)
5. [Настройка IDE](#-настройка-ide)
6. [Решение проблем](#-решение-проблем)

## 🖥️ Системные требования

### Минимальные требования:
- **ОС**: Windows 10/11, Ubuntu 20.04+, macOS 12+
- **Python**: 3.8-3.10
- **Память**: 8 GB RAM
- **GPU**: NVIDIA с 4+ GB VRAM 
- **Диск**: 10 GB свободного места

### Рекомендуемые требования:
- **GPU**: NVIDIA RTX 3060+
- **Память**: 16 GB RAM
- **Диск**: SSD NVMe

## ⚡ Быстрая установка

### 1. Клонирование репозитория
```bash
git clone <repository-url>
cd shorts-pipeline
```

### 2. Создание и активация виртуального окружения
#### Windows
```bash
python -m venv .venv
.venv\Scripts\activate
```

#### Linux/macOS
```bash
python -m venv .venv
source .venv/bin/activate
```

### 3. Установка зависимостей
```bash
   pip install -r requirements.txt
```

#### Установка правильной версии PyTorch (ВАЖНО!)
```bash
pip install torch==2.5.1+cu121 torchvision==0.20.1+cu121 torchaudio==2.5.1+cu121 --index-url https://download.pytorch.org/whl/cu121
```

## ⚙️  Авторизация в Youtube

положи OAuth JSON в auth\client_secret.json и правь config.yaml

### Запуск
```bash
python run.py
```
## 🔧 Детальная настройка
### Установка FFmpeg
   ```powershell
   choco install ffmpeg -y
   ```
#### При ошибках: 
- Скачайте FFmpeg с https://ffmpeg.org/download.html

- Распакуйте в C:\ffmpeg\

- Добавьте в системный PATH:

- Win + R → sysdm.cpl → Дополнительно → Переменные среды

- В Path добавьте: C:\ffmpeg\bin

### Установка CUDA и cuDNN
1. Скачайте CUDA Toolkit: https://developer.nvidia.com/cuda-toolkit
2. Скачайте cuDNN: https://developer.nvidia.com/cudnn
3. Скопируйте файлы cuDNN в папку CUDA:
   - bin/ → C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\vX.X\bin\
   - include/ → C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\vX.X\include\
   - lib/ → C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\vX.X\lib\

## ⚙️ Настройка IDE(чтобы можно было запускать через run в IntelliJ IDEA/PyCharm)
### 1. Настройка интерпретатора
1. File → Settings → Project → Python Interpreter
2. Выберите интерпретатор из .venv\Scripts\python.exe
3. Нажмите Apply

### 2. Настройка конфигурации запуска
1. Run → Edit Configurations
2. Создайте новую конфигурацию Python:
   - Script path: run.py
   - Python interpreter: выберите из .venv
   - Working directory: корень проекта
   - Environment variables: оставьте пустым

### 3. Отметка папок
   - Правой кнопкой на папке pipeline → Mark as → Sources Root

## 🚨 Решение проблем
### 🔧 Проблемы с CUDA и cuDNN
####   Ошибка: cuDNN failed with status CUDNN_STATUS_NOT_INITIALIZED
#### Решение:

``` bash
# Удалить текущий torch
pip uninstall torch torchvision torchaudio

# Установить совместимую версию
pip install torch==2.5.1+cu121 torchvision==0.20.1+cu121 torchaudio==2.5.1+cu121 --index-url https://download.pytorch.org/whl/cu121
```

#### Ошибка: Could not locate cudnn_ops64_9.dll
#### Решение:

1. Убедитесь, что cuDNN установлен правильно
2. Проверьте пути в переменных окружения
3. Перезагрузите систему(После добавления переменных окружения)

### 🎥 Проблемы с FFmpeg
#### Ошибка: ffmpeg не найден в PATH
#### Решение:

1. Проверить установку FFmpeg
2. Добавить в системный PATH: C:\ffmpeg\bin
3. Перезапустить IDE

### 🐍 Проблемы с Python

#### Ошибка: ImportError: attempted relative import
#### Решение: Запускать через модуль:

``` bash
python -m pipeline.main
```
Или использовать run.py

