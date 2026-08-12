# Integração Homes ↔ Sienge

Middleware em FastAPI que recebe webhooks do Homes, consulta/cadastra no
Sienge, e retorna o resultado preenchendo campos e avançando a tarefa no
Homes.

## Estrutura

```
app/
├── main.py                     # orquestrador FastAPI (entrypoint)
├── routers/
│   └── webhook_router.py       # recebe o webhook do Homes e orquestra o fluxo
├── drivers/
│   ├── base_driver.py          # retry + backoff + rate limit (compartilhado)
│   ├── sienge_driver.py        # conexão com a API do Sienge (Basic Auth)
│   └── holmes_driver.py        # conexão com a API do Homes (Bearer Token)
└── settings/
    ├── sienge_settings.py      # rate limit oficial (20 req/min)
    └── holmes_settings.py      # rate limit estimado — PENDENTE confirmação
```

`base_driver.py` concentra a lógica de sessão HTTP, retry exponencial e
rate limiting extraída de `api_requester.py`. Cada driver concreto só
implementa `_build_session()` (como autenticar) e os métodos de domínio
específicos daquele sistema.

## Como rodar localmente

**1. Instalar as dependências**

```bash
pip install -r requirements.txt
```

**2. Configurar as credenciais**

```bash
cp .env.example .env
```

Abra o `.env` e preencha `USUARIO_SIENGE`, `SENHA_SIENGE` e `TOKEN_HOMES`.

**3. Subir o servidor**

```bash
uvicorn app.main:app --reload
```

- API disponível em: `http://localhost:8000`
- Documentação automática (Swagger): `http://localhost:8000/docs`
- Health check: `http://localhost:8000/health`
- Endpoint do webhook: `POST http://localhost:8000/webhooks/homes/task`

## Disponibilizando com ngrok (para o Homes conseguir chamar seu webhook)

Enquanto o projeto roda só na sua máquina, o Homes (que está na internet)
não consegue alcançar `localhost:8000`. O ngrok cria um túnel público
temporário até sua porta local — útil para testar a integração antes de
ter um ambiente de produção definitivo.

**1. Instalar o ngrok**

```bash
# macOS (Homebrew)
brew install ngrok

# Windows (Chocolatey)
choco install ngrok

# Linux — baixar em https://ngrok.com/download
```

**2. Autenticar (uma vez só, com o token da sua conta ngrok)**

```bash
ngrok config add-authtoken SEU_TOKEN_AQUI
```

O token fica disponível em https://dashboard.ngrok.com/get-started/your-authtoken
após criar uma conta gratuita.

**3. Com o `uvicorn` já rodando na porta 8000 (passo anterior), abrir o túnel**

```bash
ngrok http 8000
```

O terminal vai mostrar algo como:

```
Forwarding    https://algo-aleatorio.ngrok-free.app -> http://localhost:8000
```

**4. Usar essa URL pública ao configurar o webhook no Homes**

```
https://algo-aleatorio.ngrok-free.app/webhooks/homes/task
```

### Pontos de atenção com o ngrok

- **A URL muda a cada vez que você reinicia o `ngrok http 8000`** no plano
  gratuito — é preciso atualizar a configuração de webhook no Homes toda
  vez, ou contratar um plano com domínio fixo (ngrok tem opção de
  subdomínio/domínio reservado pago).
- **Use só para testes/homologação.** Para produção, o ideal é expor o
  serviço num ambiente hospedado (servidor próprio, container, cloud) com
  domínio e HTTPS estáveis, não via túnel local.
- O painel `http://127.0.0.1:4040` (aberto automaticamente pelo ngrok)
  mostra em tempo real cada requisição recebida — útil para depurar o
  payload real que o Homes está enviando.

## Pendências (dependem do time do Homes)

- URL base oficial da API do Homes.
- Confirmação do limite de requisições (~100 req / 5 min, citado em
  reunião — valor em `holmes_settings.py` é uma estimativa conservadora).
- Contrato de dados real do webhook e do endpoint de atualização de tarefa
  (payload em `webhook_router.py` é um placeholder).
- Se o Sienge expõe endpoint de cadastro de credor, ou se será necessário
  RPA como alternativa.
