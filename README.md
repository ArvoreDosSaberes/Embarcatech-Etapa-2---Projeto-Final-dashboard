# Rack Inteligente Dashboard

![visitors](https://visitor-badge.laobi.icu/badge?page_id=ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware)
[![Build](https://img.shields.io/github/actions/workflow/status/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware/ci.yml?branch=main)](https://github.com/ArvoreDosSaberes/Embarcatech-Etapa-2---Projeto-Final-firmware/actions)
[![Issues](https://img.shields.io/github/issues/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware)](https://github.com/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware/issues)
[![Stars](https://img.shields.io/github/stars/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware)](https://github.com/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware/stargazers)
[![Forks](https://img.shields.io/github/forks/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware)](https://github.com/ArvoreDosSaberes.Embarcatech-Etapa-2---Projeto-Final-firmware/network/members)
[![Language](https://img.shields.io/badge/Language-C%2FC%2B%2B-brightgreen.svg)]()
[![AI Assisted](https://img.shields.io/badge/AI-Assisted-purple.svg)]()
[![Python](https://img.shields.io/badge/Python-3.x-blue.svg)](https://www.python.org/)
[![LLM](https://img.shields.io/badge/LLM-Granite-orange.svg)]()
[![License: CC BY 4.0](https://img.shields.io/badge/license-CC%20BY%204.0-blue.svg)](https://creativecommons.org/licenses/by/4.0/)
![C++](https://img.shields.io/badge/C%2B%2B-17-blue)
![CMake](https://img.shields.io/badge/CMake-%3E%3D3.16-informational)
[![Docs](https://img.shields.io/badge/docs-Doxygen-blueviolet)](docs/index.html)
[![Latest Release](https://img.shields.io/github/v/release/ArvoreDosSaberes/keyboard-menu---workspace?label=version)](https://github.com/ArvoreDosSaberes/keyboard-menu---workspace/releases/latest)
[![Contributions Welcome](https://img.shields.io/badge/contributions-welcome-success.svg)](#contribuindo)

Dashboard de monitoramento em tempo real para o projeto Rack Inteligente, desenvolvido com PyQt5.

## 📋 Descrição

Sistema de visualização e monitoramento de racks inteligentes que exibe:
- 🌡️ Temperatura em tempo real
- 💧 Umidade relativa do ar
- 📍 Localização geográfica (mapa interativo)
- 🚪 Status do rack (aberto/fechado)
- 📊 Histórico de dados em banco SQLite

## 🚀 Instalação

### Pré-requisitos

- Python 3.8 ou superior
- pip (gerenciador de pacotes Python)

### Configuração do Ambiente

1. **Clone o repositório** (se ainda não o fez):
```bash
git clone <url-do-repositorio>
cd dashboard
```

2. **Crie um ambiente virtual**:
```bash
python3 -m venv venv
source venv/bin/activate  # Linux/Mac
# ou
venv\Scripts\activate  # Windows
```

3. **Instale as dependências**:
```bash
pip install -r requirements.txt
```

4. **Configure as variáveis de ambiente**:
```bash
cp .env.example .env
```

Edite o arquivo `.env` com suas credenciais MQTT:
```ini
MQTT_SERVER=mqtt.rapport.tec.br
MQTT_PORT=1883
MQTT_USERNAME=rack
MQTT_PASSWORD=sua_senha_aqui
MQTT_KEEPALIVE=60
MQTT_BASE_TOPIC=rack/
```

## ▶️ Execução

```bash
python app.py
```

## 🏗️ Arquitetura

### Componentes Principais

- **MainWindow**: Interface gráfica principal (PyQt5)
- **MQTT Client**: Comunicação com broker MQTT (paho-mqtt)
- **SQLite Database**: Armazenamento local de histórico
- **AnalogGaugeWidget**: Visualização de temperatura e umidade
- **Leaflet Map**: Mapa interativo de localização

### Estrutura de Dados MQTT

Mensagens esperadas no tópico `rack/#`:
```json
{
  "id": 1,
  "temperatura": 25.5,
  "humidade": 60.2,
  "estado": "aberto",
  "loc": {
    "latitude": -23.550520,
    "longitude": -46.633308
  }
}
```

### Banco de Dados

Tabela `rack_data`:
```sql
CREATE TABLE rack_data (
    id INTEGER,
    latitude REAL,
    longitude REAL,
    temperatura REAL,
    estado TEXT,
    humidade REAL,
    timestamp DATETIME DEFAULT CURRENT_TIMESTAMP
)
```

## 🔧 Compatibilidade

O código inclui monkey-patches para garantir compatibilidade entre PyQt5 e a biblioteca `AnalogGaugeWidget`, que originalmente usa valores float onde PyQt5 espera int.

Classes corrigidas:
- `QPoint` - Coordenadas de pontos
- `QSize` - Dimensões
- `QRect` - Retângulos
- `QFont` - Fontes
- `QPen` - Canetas de desenho
- `QPainter` - Métodos de pintura (drawLine, drawEllipse, drawArc, drawText)

## 📝 Logs

O sistema utiliza logs formatados com emojis para facilitar debug:
- 🔌 `[MQTT/Connection]` - Conexão com broker
- 📡 `[MQTT/Subscription]` - Inscrição em tópicos
- 📨 `[MQTT/Message]` - Mensagens recebidas
- ❌ `[MQTT/Error]` - Erros de processamento

## 🛠️ Desenvolvimento

### Dependências

- `paho-mqtt>=1.6.1` - Cliente MQTT
- `PyQt5>=5.15.2` - Framework GUI
- `PyQtWebEngine>=5.15.2` - Widget de navegador web
- `QT-PyQt-PySide-Custom-Widgets>=1.0.2` - Widgets customizados
- `python-dotenv>=0.21.0` - Gerenciamento de variáveis de ambiente

### Estrutura do Projeto

```
dashboard/
├── app.py              # Aplicação principal
├── requirements.txt    # Dependências Python
├── .env.example        # Template de configuração
├── .env               # Configuração local (gitignored)
├── data.db            # Banco de dados SQLite
├── README.md          # Esta documentação
└── venv/              # Ambiente virtual Python
```

## 📄 Licença

Ver arquivo `LICENSE` para detalhes.

## 🤝 Contribuindo

1. Fork o projeto
2. Crie uma branch para sua feature (`git checkout -b feature/AmazingFeature`)
3. Commit suas mudanças (`git commit -m 'Add some AmazingFeature'`)
4. Push para a branch (`git push origin feature/AmazingFeature`)
5. Abra um Pull Request

## 📞 Suporte

Para problemas ou dúvidas, abra uma issue no repositório do projeto.
