"""
Rack Control Service

Módulo responsável pelo controle de racks via MQTT.
Contém a classe Rack que representa um rack físico e o serviço
RackControlService que envia comandos para o firmware via MQTT.

O sistema utiliza confirmação de comandos via ACK:
- Dashboard envia comando em: {base}/{rack_id}/command/{door|ventilation|buzzer}
- Firmware confirma em: {base}/{rack_id}/ack/{door|ventilation|buzzer}
- Dashboard só atualiza a UI após receber confirmação do firmware

Autor: Dashboard Rack Inteligente - EmbarcaTech
"""

import os
import time
import threading
from dataclasses import dataclass, field
from typing import Optional, Dict, Callable, Any
from enum import IntEnum


class DoorStatus(IntEnum):
    """Status da porta do rack."""
    CLOSED = 0
    OPEN = 1


class VentilationStatus(IntEnum):
    """Status da ventilação do rack."""
    OFF = 0
    ON = 1


class BuzzerStatus(IntEnum):
    """Status do buzzer/alarme do rack."""
    OFF = 0
    DOOR_OPEN = 1
    BREAK_IN = 2
    OVERHEAT = 3


@dataclass
class Rack:
    """
    Representa um rack físico no sistema.
    
    Attributes:
        rackId: Identificador único do rack
        temperature: Temperatura atual em °C
        humidity: Umidade relativa em %
        doorStatus: Status da porta (aberta/fechada)
        ventilationStatus: Status da ventilação (ligada/desligada)
        buzzerStatus: Status do alarme sonoro
        latitude: Coordenada de latitude do rack
        longitude: Coordenada de longitude do rack
    """
    rackId: str
    temperature: Optional[float] = None
    humidity: Optional[float] = None
    doorStatus: DoorStatus = DoorStatus.CLOSED
    ventilationStatus: VentilationStatus = VentilationStatus.OFF
    buzzerStatus: BuzzerStatus = BuzzerStatus.OFF
    latitude: Optional[float] = None
    longitude: Optional[float] = None
    
    def isDoorOpen(self) -> bool:
        """Verifica se a porta está aberta."""
        return self.doorStatus == DoorStatus.OPEN
    
    def isVentilationOn(self) -> bool:
        """Verifica se a ventilação está ligada."""
        return self.ventilationStatus == VentilationStatus.ON
    
    def isBuzzerActive(self) -> bool:
        """Verifica se o buzzer está ativo (qualquer estado exceto OFF)."""
        return self.buzzerStatus != BuzzerStatus.OFF


@dataclass
class PendingCommand:
    """
    Representa um comando pendente aguardando confirmação do firmware.
    
    Attributes:
        rackId: ID do rack alvo
        commandType: Tipo de comando (door, ventilation, buzzer)
        value: Valor enviado no comando
        timestamp: Momento em que o comando foi enviado
        callback: Callback a ser chamado quando confirmado (opcional)
    """
    rackId: str
    commandType: str
    value: int
    timestamp: float = field(default_factory=time.time)
    callback: Optional[Callable[[bool], None]] = None


class RackControlService:
    """
    Serviço de controle de racks via MQTT com confirmação de comandos.
    
    Envia comandos para os racks através do broker MQTT e aguarda
    confirmação (ACK) do firmware antes de atualizar o estado.
    
    Fluxo de comunicação:
    1. Dashboard publica comando em: {base}/{rack_id}/command/{type}
    2. Firmware executa o comando
    3. Firmware publica confirmação em: {base}/{rack_id}/ack/{type}
    4. Dashboard recebe ACK e atualiza a UI
    
    Attributes:
        mqttClient: Cliente MQTT para publicação de comandos
        baseTopic: Tópico base para comandos MQTT
        pendingCommands: Dicionário de comandos aguardando confirmação
        commandTimeout: Tempo limite para confirmação (segundos)
        onAckReceived: Callback chamado quando ACK é recebido
    """
    
    # Tempo limite padrão para confirmação de comandos (5 segundos)
    DEFAULT_COMMAND_TIMEOUT = 5.0
    
    def __init__(self, mqttClient, baseTopic: Optional[str] = None):
        """
        Inicializa o serviço de controle.
        
        Args:
            mqttClient: Instância do cliente MQTT (paho.mqtt.client.Client)
            baseTopic: Tópico base MQTT (default: valor de MQTT_BASE_TOPIC no .env ou 'racks')
        """
        self.mqttClient = mqttClient
        self.baseTopic = baseTopic or os.getenv("MQTT_BASE_TOPIC", "racks").rstrip("/")
        
        # Dicionário de comandos pendentes: chave = "{rackId}:{commandType}"
        self.pendingCommands: Dict[str, PendingCommand] = {}
        self._pendingLock = threading.Lock()
        
        # Tempo limite para confirmação de comandos
        self.commandTimeout = float(os.getenv("COMMAND_ACK_TIMEOUT", str(self.DEFAULT_COMMAND_TIMEOUT)))
        
        # Callback externo para notificar quando ACK é recebido
        self.onAckReceived: Optional[Callable[[str, str, int, bool], None]] = None
    
    def _getPendingKey(self, rackId: str, commandType: str) -> str:
        """
        Gera a chave única para um comando pendente.
        
        Args:
            rackId: ID do rack
            commandType: Tipo de comando
            
        Returns:
            str: Chave no formato "{rackId}:{commandType}"
        """
        return f"{rackId}:{commandType}"
    
    def _publishCommand(self, rack: Rack, commandType: str, value: int, 
                        callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Publica um comando MQTT para o rack especificado.
        
        O comando é registrado como pendente até receber confirmação
        do firmware. A UI NÃO é atualizada até o ACK ser recebido.
        
        Args:
            rack: Instância do rack alvo
            commandType: Tipo de comando (door, ventilation, buzzer)
            value: Valor do comando
            callback: Função opcional chamada quando ACK recebido
            
        Returns:
            bool: True se publicado com sucesso, False caso contrário
        """
        if self.mqttClient is None:
            print(f"[RackControlService/Error] ❌ MQTT client not initialized")
            return False
        
        topic = f"{self.baseTopic}/{rack.rackId}/command/{commandType}"
        try:
            result = self.mqttClient.publish(topic, str(value))
            if result.rc == 0:
                # Registra comando como pendente aguardando ACK
                pendingKey = self._getPendingKey(rack.rackId, commandType)
                with self._pendingLock:
                    self.pendingCommands[pendingKey] = PendingCommand(
                        rackId=rack.rackId,
                        commandType=commandType,
                        value=value,
                        timestamp=time.time(),
                        callback=callback
                    )
                print(f"[RackControlService/Command] 📤 Sent {commandType}={value} to rack {rack.rackId} (awaiting ACK)")
                return True
            else:
                print(f"[RackControlService/Error] ❌ Failed to publish: rc={result.rc}")
                return False
        except Exception as e:
            print(f"[RackControlService/Error] ❌ Exception publishing command: {e}")
            return False
    
    def processAck(self, rackId: str, commandType: str, value: int) -> bool:
        """
        Processa uma confirmação (ACK) recebida do firmware.
        
        Remove o comando da lista de pendentes e notifica via callback.
        
        Args:
            rackId: ID do rack que confirmou
            commandType: Tipo de comando confirmado (door, ventilation, buzzer)
            value: Valor confirmado pelo firmware
            
        Returns:
            bool: True se havia comando pendente correspondente
        """
        pendingKey = self._getPendingKey(rackId, commandType)
        pendingCmd = None
        
        with self._pendingLock:
            if pendingKey in self.pendingCommands:
                pendingCmd = self.pendingCommands.pop(pendingKey)
        
        if pendingCmd:
            success = (pendingCmd.value == value)
            print(f"[RackControlService/ACK] ✅ Received ACK for {commandType}={value} from rack {rackId}")
            
            # Chama callback do comando se existir
            if pendingCmd.callback:
                try:
                    pendingCmd.callback(success)
                except Exception as e:
                    print(f"[RackControlService/Error] ❌ Callback error: {e}")
            
            # Notifica callback externo
            if self.onAckReceived:
                try:
                    self.onAckReceived(rackId, commandType, value, success)
                except Exception as e:
                    print(f"[RackControlService/Error] ❌ External callback error: {e}")
            
            return True
        else:
            print(f"[RackControlService/ACK] ⚠️ Unexpected ACK for {commandType}={value} from rack {rackId} (no pending command)")
            return False
    
    def hasPendingCommand(self, rackId: str, commandType: str) -> bool:
        """
        Verifica se há comando pendente para um rack/tipo específico.
        
        Args:
            rackId: ID do rack
            commandType: Tipo de comando
            
        Returns:
            bool: True se há comando pendente
        """
        pendingKey = self._getPendingKey(rackId, commandType)
        with self._pendingLock:
            return pendingKey in self.pendingCommands
    
    def getExpiredCommands(self) -> list:
        """
        Retorna lista de comandos que expiraram (sem ACK no tempo limite).
        
        Returns:
            list: Lista de PendingCommand expirados
        """
        expired = []
        currentTime = time.time()
        
        with self._pendingLock:
            expiredKeys = []
            for key, cmd in self.pendingCommands.items():
                if currentTime - cmd.timestamp > self.commandTimeout:
                    expired.append(cmd)
                    expiredKeys.append(key)
            
            # Remove comandos expirados
            for key in expiredKeys:
                del self.pendingCommands[key]
                
        return expired
    
    def clearPendingCommands(self, rackId: Optional[str] = None):
        """
        Limpa comandos pendentes.
        
        Args:
            rackId: Se especificado, limpa apenas comandos deste rack.
                    Se None, limpa todos os comandos pendentes.
        """
        with self._pendingLock:
            if rackId is None:
                self.pendingCommands.clear()
            else:
                keysToRemove = [k for k in self.pendingCommands 
                               if k.startswith(f"{rackId}:")]
                for key in keysToRemove:
                    del self.pendingCommands[key]
    
    def openDoor(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Abre a porta do rack.
        
        O estado da porta NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "door", DoorStatus.OPEN, callback)
    
    def closeDoor(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Fecha a porta do rack.
        
        O estado da porta NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "door", DoorStatus.CLOSED, callback)
    
    def toggleDoor(self, rack: Rack) -> bool:
        """
        Alterna o estado da porta (abre se fechada, fecha se aberta).
        
        Args:
            rack: Instância do rack
            
        Returns:
            bool: True se comando enviado com sucesso
        """
        if rack.isDoorOpen():
            return self.closeDoor(rack)
        else:
            return self.openDoor(rack)
    
    def turnOnVentilation(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Liga a ventilação do rack.
        
        O estado da ventilação NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "ventilation", VentilationStatus.ON, callback)
    
    def turnOffVentilation(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Desliga a ventilação do rack.
        
        O estado da ventilação NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "ventilation", VentilationStatus.OFF, callback)
    
    def toggleVentilation(self, rack: Rack) -> bool:
        """
        Alterna o estado da ventilação (liga se desligada, desliga se ligada).
        
        Args:
            rack: Instância do rack
            
        Returns:
            bool: True se comando enviado com sucesso
        """
        if rack.isVentilationOn():
            return self.turnOffVentilation(rack)
        else:
            return self.turnOnVentilation(rack)
    
    def activateCriticalTemperatureAlert(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Ativa o alerta de temperatura crítica (superaquecimento).
        
        O estado do buzzer NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "buzzer", BuzzerStatus.OVERHEAT, callback)
    
    def deactivateCriticalTemperatureAlert(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Desativa o alerta de temperatura crítica.
        
        O estado do buzzer NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "buzzer", BuzzerStatus.OFF, callback)
    
    def activateDoorOpenAlert(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Ativa o alerta de porta aberta.
        
        O estado do buzzer NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "buzzer", BuzzerStatus.DOOR_OPEN, callback)
    
    def activateBreakInAlert(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Ativa o alerta de arrombamento.
        
        O estado do buzzer NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self._publishCommand(rack, "buzzer", BuzzerStatus.BREAK_IN, callback)
    
    def silenceBuzzer(self, rack: Rack, callback: Optional[Callable[[bool], None]] = None) -> bool:
        """
        Silencia o buzzer do rack.
        
        O estado do buzzer NÃO é atualizado imediatamente.
        A atualização ocorre apenas quando o firmware confirma via ACK.
        
        Args:
            rack: Instância do rack
            callback: Função chamada quando ACK recebido (opcional)
            
        Returns:
            bool: True se comando enviado com sucesso (não significa executado)
        """
        return self.deactivateCriticalTemperatureAlert(rack, callback)
    
    # Métodos com nomes em português para compatibilidade
    def abrirPorta(self, rack: Rack) -> bool:
        """Alias para openDoor()."""
        return self.openDoor(rack)
    
    def fecharPorta(self, rack: Rack) -> bool:
        """Alias para closeDoor()."""
        return self.closeDoor(rack)
    
    def acionarVentilador(self, rack: Rack) -> bool:
        """Alias para turnOnVentilation()."""
        return self.turnOnVentilation(rack)
    
    def desligarVentilador(self, rack: Rack) -> bool:
        """Alias para turnOffVentilation()."""
        return self.turnOffVentilation(rack)
    
    def gerarAlertaTemperaturaCritica(self, rack: Rack) -> bool:
        """Alias para activateCriticalTemperatureAlert()."""
        return self.activateCriticalTemperatureAlert(rack)
    
    def desativarAlertaTemperaturaCritica(self, rack: Rack) -> bool:
        """Alias para deactivateCriticalTemperatureAlert()."""
        return self.deactivateCriticalTemperatureAlert(rack)
