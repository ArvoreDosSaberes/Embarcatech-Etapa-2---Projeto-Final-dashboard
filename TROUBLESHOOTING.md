# Troubleshooting - Rack Inteligente Dashboard

Guia de resolução de problemas comuns do Dashboard.

## 📋 Índice

- [Problemas de Instalação](#problemas-de-instalação)
- [Problemas de Conexão MQTT](#problemas-de-conexão-mqtt)
- [Problemas de Interface Gráfica](#problemas-de-interface-gráfica)
- [Problemas de Banco de Dados](#problemas-de-banco-de-dados)
- [Logs e Debug](#logs-e-debug)

---

## Problemas de Instalação

### Erro: "Python 3 is not installed"

**Sintoma:**
```bash
bash: python3: command not found
```

**Solução:**
```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3 python3-pip python3-venv

# Fedora/RHEL
sudo dnf install python3 python3-pip

# Arch Linux
sudo pacman -S python python-pip
```

### Erro: "No module named 'PyQt5'"

**Sintoma:**
```
ModuleNotFoundError: No module named 'PyQt5'
```

**Solução:**
```bash
# Ative o ambiente virtual
source venv/bin/activate

# Reinstale as dependências
pip install -r requirements.txt
```

### Erro: "xcb platform plugin" no Linux

**Sintoma:**
```
qt.qpa.plugin: Could not load the Qt platform plugin "xcb"
```

**Solução:**
```bash
# Ubuntu/Debian
sudo apt install libxcb-xinerama0 libxcb-cursor0

# Fedora
sudo dnf install xcb-util-cursor
```

---

## Problemas de Conexão MQTT

### Erro: "MQTT_SERVER not configured"

**Sintoma:**
```
ValueError: MQTT_SERVER not configured in .env file
```

**Solução:**
1. Copie o arquivo de exemplo:
```bash
cp .env.example .env
```

2. Edite o arquivo `.env` com suas credenciais:
```bash
nano .env
```

3. Configure os valores:
```ini
MQTT_SERVER=mqtt.rapport.tec.br
MQTT_PORT=1883
MQTT_USERNAME=rack
MQTT_PASSWORD=sua_senha_aqui
MQTT_KEEPALIVE=60
MQTT_BASE_TOPIC=rack/
```

### Erro: "Invalid host"

**Sintoma:**
```
ValueError: Invalid host.
```

**Causas Possíveis:**
- Arquivo `.env` não existe
- Variável `MQTT_SERVER` está vazia ou com valor inválido

**Solução:**
1. Verifique se o arquivo `.env` existe
2. Confirme que `MQTT_SERVER` tem um valor válido (sem espaços extras)
3. Teste a conectividade:
```bash
ping mqtt.rapport.tec.br
```

### Erro: "Connection refused" (código 1)

**Sintoma:**
```
[MQTT/Connection] 🔌 Connected with result code: 1
```

**Causas:**
- Servidor MQTT está offline
- Porta bloqueada por firewall
- Credenciais incorretas

**Solução:**
1. Teste a conexão com o servidor:
```bash
telnet mqtt.rapport.tec.br 1883
```

2. Verifique as credenciais no arquivo `.env`

3. Execute o teste de conexão:
```bash
python test_mqtt.py
```

### Erro: "Bad username or password" (código 4)

**Sintoma:**
```
[MQTT/Connection] 🔌 Connected with result code: 4
```

**Solução:**
Verifique as credenciais no arquivo `.env`:
- `MQTT_USERNAME` está correto?
- `MQTT_PASSWORD` está correto?
- Não há espaços extras antes ou depois dos valores?

---

## Problemas de Interface Gráfica

### Erro: "TypeError: arguments did not match any overloaded call"

**Sintoma:**
```
TypeError: arguments did not match any overloaded call:
  QSize(w: int, h: int): argument 1 has unexpected type 'float'
```

**Solução:**
Este erro já foi corrigido no código com monkey-patches. Se ainda ocorrer:
1. Atualize o código para a versão mais recente
2. Reinstale as dependências:
```bash
pip install --upgrade -r requirements.txt
```

### Gauges não aparecem ou aparecem em branco

**Causas:**
- Widget AnalogGaugeWidget não foi instalado corretamente
- Problema de compatibilidade com PyQt5

**Solução:**
```bash
pip uninstall QT-PyQt-PySide-Custom-Widgets
pip install QT-PyQt-PySide-Custom-Widgets>=1.0.2
```

### Mapa não carrega

**Causas:**
- Sem conexão com internet
- Leaflet CDN está offline
- Coordenadas inválidas

**Solução:**
1. Verifique a conexão com internet
2. Teste o acesso ao CDN:
```bash
curl -I https://unpkg.com/leaflet/dist/leaflet.css
```

3. Verifique se as coordenadas estão no formato correto (latitude, longitude)

---

## Problemas de Banco de Dados

### Erro: "database is locked"

**Sintoma:**
```
sqlite3.OperationalError: database is locked
```

**Causas:**
- Múltiplas instâncias do aplicativo rodando
- Arquivo de banco corrompido

**Solução:**
1. Feche todas as instâncias do aplicativo
2. Se persistir, remova o arquivo de lock:
```bash
rm data.db-journal
```

3. Em último caso, recrie o banco:
```bash
rm data.db
# O banco será recriado na próxima execução
```

### Dados não aparecem na lista de racks

**Causas:**
- Nenhuma mensagem MQTT foi recebida ainda
- Banco de dados vazio

**Solução:**
1. Verifique se está recebendo mensagens MQTT:
```bash
python test_mqtt.py
```

2. Verifique o banco de dados:
```bash
sqlite3 data.db "SELECT * FROM rack_data;"
```

---

## Logs e Debug

### Ativando modo verbose

Para mais informações de debug, monitore a saída do console:

```bash
python app.py 2>&1 | tee dashboard.log
```

### Interpretando os logs

Os logs seguem o formato:
```
[Setor/Categoria] 🔰 Mensagem
```

**Setores:**
- `[App/*]` - Aplicação principal
- `[MQTT/*]` - Cliente MQTT
- `[DB/*]` - Banco de dados
- `[UI/*]` - Interface gráfica

**Emojis:**
- ✅ - Sucesso
- ❌ - Erro
- ⚠️ - Aviso
- ℹ️ - Informação
- 🔌 - Conexão
- 📡 - Subscrição
- 📨 - Mensagem recebida
- 💾 - Banco de dados
- 🚀 - Inicialização
- 🛑 - Encerramento

### Testando componentes individualmente

**Teste de conexão MQTT:**
```bash
python test_mqtt.py
```

**Teste de banco de dados:**
```bash
sqlite3 data.db
sqlite> .tables
sqlite> SELECT * FROM rack_data LIMIT 5;
sqlite> .quit
```

**Verificar variáveis de ambiente:**
```bash
source venv/bin/activate
python -c "from dotenv import load_dotenv; import os; load_dotenv(); print(f'Server: {os.getenv(\"MQTT_SERVER\")}')"
```

---

## Problemas Conhecidos

### PySide6 no requirements.txt

O arquivo `requirements.txt` inclui `PySide6==6.9.1`, mas o projeto usa PyQt5. Isso não causa conflito, mas pode ser removido se desejar:

```bash
pip uninstall PySide6
```

### Monkey-patches para compatibilidade

O código inclui vários monkey-patches para compatibilidade entre PyQt5 e AnalogGaugeWidget. Estes são necessários e não devem ser removidos.

---

## Obtendo Ajuda

Se o problema persistir:

1. **Verifique os logs** - Execute com `python app.py` e observe as mensagens
2. **Teste componentes** - Use `test_mqtt.py` para isolar problemas
3. **Documente o erro** - Copie a mensagem de erro completa
4. **Abra uma issue** - No repositório do projeto com:
   - Descrição do problema
   - Mensagens de erro completas
   - Versão do Python (`python3 --version`)
   - Sistema operacional
   - Passos para reproduzir

---

## Comandos Úteis de Diagnóstico

```bash
# Verificar versão do Python
python3 --version

# Verificar pacotes instalados
pip list

# Verificar conectividade MQTT
telnet mqtt.rapport.tec.br 1883

# Verificar processos Python rodando
ps aux | grep python

# Limpar cache Python
find . -type d -name __pycache__ -exec rm -rf {} +
find . -type f -name "*.pyc" -delete

# Recriar ambiente virtual
rm -rf venv
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```
