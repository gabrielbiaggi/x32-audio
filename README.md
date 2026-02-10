# X32 Live Stream Automation System

Sistema distribuído para automação de mixagem da Behringer X32 focado em Live Stream, utilizando arquitetura Edge-Core via MQTT.

## arquitetura

```mermaid
graph TD
    subgraph "Igreja (Edge)"
        X32[Mesa X32] -- USB (Áudio 32ch) --> Laptop
        X32 -- UDP (OSC) --> Laptop
        Laptop[Edge Node (Python)]
    end

    subgraph "Casa/Server (Core)"
        Mosquitto[MQTT Broker]
        Brain[Brain Core (Python)]
    end

    Laptop -- VPN/Internet (MQTT) --> Mosquitto
    Mosquitto <--> Brain
```

## Estrutura do Projeto

- **`src/edge_node.py`**: Roda no Laptop (Igreja). Lê áudio e envia telemetria. Recebe comandos e aplica na X32.
- **`src/brain_core.py`**: Roda no Servidor (Casa). Processa lógica de Auto-Leveling e Ducking.
- **`config/x32_map.json`**: Mapeamento dos canais, grupos e prioridades.
- **`docker-compose.yml`**: Sobe o Broker MQTT e o Brain (opcional).

## Como Rodar

### 1. Preparação (Ambos os lados)

Clone o repositório:

```bash
git clone https://github.com/seu-usuario/x32-audio.git
cd x32-audio
pip install -r requirements.txt
```

### 2. No Servidor (Core / Brain)

Suba a infraestrutura (recomenda-se usar Docker para o Broker):

```bash
docker-compose up -d
```

O Brain pode rodar via Docker (já configurado no compose) ou manual:

```bash
python src/brain_core.py
```

### 3. No Notebook (Edge / Igreja)

O notebook precisa ter acesso ao IP do servidor (via VPN Tailscale, por exemplo).

**Comando de Inicialização:**

```bash
# --broker: IP do seu servidor (Brain)
# --x32: IP da mesa X32
python src/edge_node.py --broker 100.x.x.x --x32 192.168.1.10
```

> **Nota:** Certifique-se que o driver da X-USB está instalado e a mesa está conectada via USB _antes_ de rodar o script.

## Funcionalidades

- **Auto-Leveling (Vocais)**: Mantém os vocais próximos a -18dBFS no bus da Live.
- **Ducking (Speech)**: Se alguém falar nos microfones de "Speech" (Pastores), a banda/bateria abaixa automaticamente 4dB.
- **Human Override**: Se você mexer num fader fisicamente, a automação daquele canal pausa por 5 segundos.
