# Changelog - Rack Inteligente Dashboard

Histórico de mudanças e correções do projeto.

---

## [1.2.4] - 2025-12-02

### 🆕 Adicionado

#### Umidade como Variável Exógena para Previsão de Temperatura
- **Correção de umidade**: Umidade afeta dissipação de calor do rack
- **Modelo de impacto**: 0.05°C por % de umidade acima de 50%
- **Projeção de tendência**: Extrapola umidade futura baseada em histórico
- Novo método: `applyHumidityCorrection()`
- Parâmetro `exogenousData` no método `predict()`

---

## [1.2.2] - 2025-12-02

### 🆕 Adicionado

#### Previsão de 24 Horas com Sazonalidade Anual
- **Horizonte de previsão**: 24 horas futuras
- **Histórico analisado**: 7 dias (168 horas) de dados
- **Sazonalidade diária**: Ciclo de 24 horas
- **Sazonalidade anual**: Previsão climática com variação sazonal (verão/inverno)
- **Agregação de dados**: Amostras em segundos → médias horárias
- Novos métodos: `aggregateHourlyData()`, `addAnnualSeasonalComponent()`

---

## [1.2.0] - 2025-12-02

### 🆕 Adicionado

#### Sistema de Fallback SARIMA para Previsão de Séries Temporais
- **Novo serviço**: `sarimaFallbackService.py` implementando modelo SARIMA completo
- **Arquitetura híbrida**: Granite TTM como modelo principal, SARIMA como fallback
- **Troca automática**: Baseada na métrica MAE (Mean Absolute Error)
- **Documentação**: Baseado no artigo "MAE e SARIMA como fallback na falta do Granite TTM"

#### Funcionalidades do SarimaFallbackService
- Implementação SARIMA(p,d,q)(P,D,Q)_s com parâmetros configuráveis
- Detecção automática de sazonalidade via análise de autocorrelação
- Suporte a statsmodels para SARIMA otimizado
- Implementação simplificada como fallback quando statsmodels indisponível
- Cálculo de MAE em janela deslizante para monitoramento contínuo
- Thread-safety com locks para operações concorrentes
- Saída graciosa via handlers de SIGINT/SIGTERM

#### Integração com ForecastService
- SARIMA como fallback primário (substitui Exponential Smoothing)
- Exponential Smoothing como fallback secundário (se SARIMA falhar)
- Monitoramento contínuo de MAE para decisão de troca
- Métodos `updateMaeTracking()`, `shouldUseFallback()`, `getFallbackInfo()`
- Configuração via variáveis de ambiente

#### Integração com app.py (Dashboard Principal)
- Substituição da previsão linear simples pelo `ForecastService`
- Novo método `initializeForecastService()` para inicialização do serviço
- `update_metric_forecast()` agora usa Granite TTM/SARIMA com fallback linear
- Shutdown gracioso do `ForecastService` no `closeEvent()`
- Remoção de código morto da previsão linear antiga (mantido como fallback)

#### Novas Variáveis de Ambiente
- `FORECAST_MAE_THRESHOLD`: Limiar de MAE para ativar fallback (default: 5.0)
- `FORECAST_SEASONAL_PERIOD`: Período sazonal para SARIMA (default: 24)

#### Novas Dependências
- `pandas>=1.5.0`: Manipulação de séries temporais
- `numpy>=1.21.0`: Operações numéricas
- `statsmodels>=0.14.0`: Modelo SARIMA otimizado

### 📖 Referência
Implementação baseada no artigo: `docs/Tutoriais/MAE-e-SARIMA-como-falback-na-falta-do-granite-ttm.md`

---

## [1.1.0] - 2025-01-03

### ✅ Corrigido

#### Compatibilidade PyQt5 / AnalogGaugeWidget
- **Problema**: Biblioteca `AnalogGaugeWidget` usa valores `float` onde PyQt5 espera `int`
- **Solução**: Implementados monkey-patches abrangentes para:
  - `QPoint` - Conversão de coordenadas float para int
  - `QSize` - Conversão de dimensões float para int
  - `QRect` - Conversão de retângulos float para int
  - `QFont` - Conversão de tamanho de fonte float para int
  - `QPen` - Conversão de largura de caneta float para int
  - `QPainter.drawLine()` - Conversão de coordenadas de linha
  - `QPainter.drawEllipse()` - Conversão de coordenadas de elipse
  - `QPainter.drawArc()` - Conversão de coordenadas de arco
  - `QPainter.drawText()` - Conversão de coordenadas de texto

#### MQTT Client API Deprecation
- **Problema**: `DeprecationWarning: Callback API version 1 is deprecated`
- **Solução**: Atualizado para usar `mqtt.CallbackAPIVersion.VERSION2`
- **Mudanças**:
  - `mqtt.Client()` → `mqtt.Client(callback_api_version=mqtt.CallbackAPIVersion.VERSION2)`
  - Callback `on_connect()` agora aceita parâmetro `properties=None`

#### Validação de Configuração
- **Problema**: `ValueError: Invalid host` quando `.env` não existe
- **Solução**: Adicionada validação de variáveis de ambiente com mensagens claras
- **Comportamento**: Aplicação falha rapidamente com mensagem instrutiva se configuração estiver faltando

### 🆕 Adicionado

#### Tratamento de Exceções
- Adicionado try-except em todos os métodos críticos:
  - `handle_message()` - Atualização de UI
  - `on_message()` - Processamento de mensagens MQTT
  - `on_rack_selected()` - Seleção de rack
  - `closeEvent()` - Limpeza de recursos

#### Logs Estruturados
- Sistema de logs com emojis e categorização:
  - `[MQTT/Connection]` 🔌 - Status de conexão
  - `[MQTT/Subscription]` 📡 - Inscrições em tópicos
  - `[MQTT/Message]` 📨 - Mensagens recebidas
  - `[MQTT/Error]` ❌ - Erros MQTT
  - `[UI/Error]` ❌ - Erros de interface
  - `[DB/Info]` ℹ️ - Informações do banco
  - `[DB/Error]` ❌ - Erros de banco
  - `[App/Start]` 🚀 - Inicialização
  - `[App/Ready]` ✅ - Aplicação pronta
  - `[App/Shutdown]` 🛑 - Encerramento

#### Cleanup de Recursos
- Método `closeEvent()` para limpeza adequada:
  - Desconexão do cliente MQTT
  - Fechamento da conexão com banco de dados
  - Tratamento de erros durante cleanup

#### Melhorias na UI
- Formatação de valores com uma casa decimal (ex: `25.5°C`, `60.2%`)
- Correção de typo: "Situação do Hack" → "Situação do Rack"
- Validação de tipos antes de atualizar gauges

#### Scripts de Automação
- `setup.sh` - Script de instalação automatizada
  - Cria ambiente virtual
  - Instala dependências
  - Configura arquivo `.env`
  - Validações de pré-requisitos
  
- `run.sh` - Script de execução rápida
  - Valida ambiente virtual
  - Valida arquivo `.env`
  - Ativa venv e executa aplicação

- `test_mqtt.py` - Ferramenta de teste de conexão MQTT
  - Testa conectividade com broker
  - Valida credenciais
  - Monitora mensagens em tempo real

#### Documentação
- **README.md** completo com:
  - Descrição do projeto
  - Instruções de instalação
  - Guia de execução
  - Documentação de arquitetura
  - Estrutura de dados MQTT
  - Schema do banco de dados
  - Informações sobre compatibilidade

- **TROUBLESHOOTING.md** - Guia de resolução de problemas:
  - Problemas de instalação
  - Problemas de conexão MQTT
  - Problemas de interface gráfica
  - Problemas de banco de dados
  - Interpretação de logs
  - Comandos úteis de diagnóstico

- **CHANGELOG.md** - Este arquivo

### 🔧 Modificado

#### Estrutura do Código
- Reorganização dos imports para melhor clareza
- Documentação inline com docstrings em todos os métodos
- Separação clara entre monkey-patches e código da aplicação

#### Tratamento de Erros no Main
- Captura de `KeyboardInterrupt` para encerramento limpo
- Captura de exceções gerais com traceback completo
- Códigos de saída apropriados (0, 1)

### 📦 Dependências

Versões confirmadas e testadas:
- `paho-mqtt>=1.6.1` - Cliente MQTT com suporte a API v2
- `PyQt5>=5.15.2` - Framework GUI
- `PyQtWebEngine>=5.15.2` - Widget de navegador web
- `QT-PyQt-PySide-Custom-Widgets>=1.0.2` - Widgets customizados (AnalogGaugeWidget)
- `python-dotenv>=0.21.0` - Gerenciamento de variáveis de ambiente

### 🐛 Bugs Conhecidos Resolvidos

1. ✅ `TypeError: QSize(): argument 1 has unexpected type 'float'`
2. ✅ `TypeError: drawLine(): argument 1 has unexpected type 'float'`
3. ✅ `TypeError: QFont(): argument 2 has unexpected type 'float'`
4. ✅ `ValueError: Invalid host` quando `.env` não existe
5. ✅ `DeprecationWarning: Callback API version 1 is deprecated`

---

## [1.0.0] - Data Anterior

### Inicial
- Implementação básica do dashboard
- Integração com MQTT
- Visualização de temperatura e umidade
- Mapa de localização
- Banco de dados SQLite

---

## Formato de Versionamento

Este projeto segue o [Semantic Versioning](https://semver.org/):

- **MAJOR** (x.0.0): Mudanças incompatíveis com versões anteriores
- **MINOR** (0.x.0): Novas funcionalidades mantendo compatibilidade
- **PATCH** (0.0.x): Correções de bugs mantendo compatibilidade

---

## Tipos de Mudanças

- **Adicionado** - Novas funcionalidades
- **Modificado** - Mudanças em funcionalidades existentes
- **Descontinuado** - Funcionalidades que serão removidas
- **Removido** - Funcionalidades removidas
- **Corrigido** - Correções de bugs
- **Segurança** - Correções de vulnerabilidades
