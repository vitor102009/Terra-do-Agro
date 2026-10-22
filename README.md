# Terra do Agro

**A Revolução Digital do Agro Brasileiro**

Plataforma rural com gestão da propriedade (rebanho, pesagens, sanidade, reprodução, lotes e pastagens, insumos, financeiro, custos e relatórios), marketplace, contratação de profissionais, vagas no campo, chat, grupos, feed, notificações, clima, cotações, painel administrativo e aplicativo instalável (PWA) com registro offline.

---

## 1. Instalação no Windows (terminal do VS Code)

Pré-requisito: Python 3.11 ou mais recente (testado com 3.14).

```powershell
cd C:\Users\<voce>\Desktop\TerradoAgro
python -m venv .venv
.venv\Scripts\Activate.ps1          # se o PowerShell bloquear: Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
pip install -r requirements.txt
copy .env.example .env              # ajuste os valores se necessário
flask --app app setup               # cria/atualiza o banco (migrações) e cadastra categorias e grupos padrão
flask --app app create-admin        # cria o administrador (a senha é digitada de forma oculta)
python app.py                       # ou: flask --app app run --debug
```

Acesse **http://127.0.0.1:5000**.

### Dados de demonstração (opcional, só em desenvolvimento)

```powershell
flask --app app demo-data --confirmar   # cria produtor e veterinária fictícios; a senha é exibida uma única vez
flask --app app remover-demo            # remove tudo o que foi marcado como demonstração
```

Todos os registros de demonstração têm `is_demo` e aparecem com o selo "Demonstração". O comando é bloqueado com `FLASK_ENV_MODE=production`.

## 2. Comandos úteis

| Comando | O que faz |
|---|---|
| `flask --app app setup` | Aplica as migrações (nunca apaga dados) e cadastra os dados de referência |
| `flask --app app db migrate -m "msg"` / `db upgrade` | Gera e aplica novas migrações após alterar modelos |
| `flask --app app create-admin` | Cria ou promove um administrador. Também aceita `ADMIN_PASSWORD` no ambiente |
| `flask --app app backup [--com-arquivos]` | Cópia consistente do SQLite (API de backup) em `backups/`, e opcionalmente das fotos e anexos |
| `flask --app app limpar-registros --dias 90` | Política de retenção: tentativas de login e registros de sincronização antigos |

**Backup recomendado:** agende `flask --app app backup --com-arquivos` diariamente (Agendador de Tarefas do Windows) e copie a pasta `backups/` para outro disco ou para a nuvem. Para restaurar, pare o servidor e substitua `instance/terradoagro.db` pela cópia desejada.

## 3. Testes

```powershell
pip install -r requirements-dev.txt
python -m pytest -q
```

50 testes automatizados cobrem cadastro, login e logout, recuperação de senha de uso único, limite de tentativas, CSRF, permissões por papel, isolamento entre usuários e propriedades, rebanho, pesagens e GMD, vacinação e agenda, reprodução, estoque, financeiro, custos sem duplicação, relatórios, marketplace e uploads, busca de profissionais, verificação e contratação completa, vagas e candidaturas, chat idempotente e bloqueio, feed e grupos, administração e moderação, páginas 404 e 500, manifesto e Service Worker, sincronização offline e comandos de linha de comando.

`tests/test_navegador_offline.py` abre o **Microsoft Edge real** (via Playwright) e executa o fluxo completo: instalação do Service Worker, abertura sem internet, consulta offline, registro de pesagem offline, queda de conexão durante o envio (o servidor grava, mas a resposta se perde) e reenvio sem duplicar, além de limpeza ao sair da conta e layout em tela de celular sem rolagem horizontal. Sem Edge ou Playwright, o teste é ignorado.

## 4. Arquitetura

```
app.py, config.py              ponto de entrada e configuração (.env)
app/__init__.py                application factory, segurança (CSP, cabeçalhos), erros 403/404/413/500
app/models/                    51 tabelas: usuários, propriedades, rebanho, financeiro, mercado, social, sistema
app/services/                  regras de negócio: indicadores, custos, estoque, alertas, clima, rebanho
app/<módulo>/routes.py         um blueprint por módulo (auth, propriedades, animais, lotes, pesagens, saude,
                               reproducao, insumos, financeiro, relatorios, marketplace, profissionais,
                               servicos, vagas, chat, comunidade, feed, notificacoes, clima, admin, api)
app/templates/                 Jinja2 + Bootstrap 5 (local), macros de formulário em _macros.html
app/static/                    CSS da identidade visual, JS sem scripts inline, ícones, bibliotecas locais
migrations/                    Alembic (Flask-Migrate)
tests/                         pytest + teste de navegador
```

Tecnologias: Flask, SQLAlchemy, SQLite, Flask-Login, Flask-WTF (CSRF), Flask-Migrate, Bootstrap 5, Bootstrap Icons e Chart.js, todos servidos localmente para funcionar sem internet.

## 5. Segurança e privacidade (LGPD)

- Senhas com hash (Werkzeug/PBKDF2). Sessões invalidadas ao trocar a senha ou ao suspender a conta.
- CSRF em todos os formulários e na API. Limite de 5 tentativas de login por e-mail ou IP a cada 15 minutos. Mensagens de erro genéricas.
- Administrador criado somente pela linha de comando. O campo `is_admin` não é aceito em formulários.
- Permissões por propriedade: **Proprietário**, **Gerente**, **Operador** (sem financeiro) e **Consulta**. Registros de outra propriedade retornam 404.
- Uploads validados por extensão, tamanho e assinatura do conteúdo. Documentos e anexos ficam em área privada, servidos apenas a quem tem permissão.
- Content-Security-Policy restritiva (sem scripts inline), `X-Frame-Options`, `nosniff` e `Cache-Control: no-store` nas páginas autenticadas.
- Localização da propriedade, rebanho individual e financeiro nunca aparecem em páginas públicas nem no painel administrativo. O telefone do trabalhador só aparece com consentimento explícito.
- Titular dos dados: exportação em JSON e pedidos de correção ou exclusão em *Minha conta*.
- Ações administrativas registradas em auditoria.
- Em produção: defina `FLASK_ENV_MODE=production`, `SECRET_KEY` forte e, atrás de HTTPS, `SESSION_COOKIE_SECURE=true`. Use um servidor WSGI no lugar do servidor de desenvolvimento (por exemplo, `pip install waitress` e depois `waitress-serve --port=8000 --call app:create_app` no Windows).

## 6. Critérios de cálculo

- **Receitas e despesas do período:** lançamentos não cancelados pela *data de competência*.
- **Fluxo de caixa:** pagamentos e recebimentos efetivos, pela data em que ocorreram. O saldo de caixa é calculado pelos lançamentos registrados.
- **Contas a pagar e a receber:** valor menos o já pago, aceitando pagamentos parciais.
- **GMD:** (último peso − primeiro peso) ÷ dias, com pelo menos 2 pesagens individuais e 7 dias de intervalo. Sem isso, aparece "dados insuficientes".
- **Taxa de prenhez:** gestantes ÷ (vazias + cobertas aguardando diagnóstico + gestantes).
- **Previsão de parto:** data do último serviço + duração média de gestação da espécie (bovino 283 dias). É apenas uma estimativa, calculada somente após diagnóstico positivo informado por um responsável.
- **Lotação (UA/ha):** peso vivo ÷ 450 kg ÷ área. Só é exibida quando todos os animais têm pesagem e a área é conhecida. Não é estimativa de capacidade de suporte.
- **Custos de produção:** despesas de categorias marcadas como custo. Compras de estoque entram pelo **consumo** (saídas valorizadas pelo custo médio), nunca pela compra, para não contar duas vezes. Custos sem lote nem animal são rateados pelo número de animais ativos de cada lote.
- **Estoque:** custo médio ponderado. A despesa é lançada apenas na entrada (opcional, uma única vez). Saídas não geram nova despesa.

## 7. Aplicativo (PWA) e modo offline

**Instalação:** no Chrome ou Edge (computador e Android) aparece a opção "Instalar aplicativo". No iPhone, use Safari › Compartilhar › Adicionar à Tela de Início.

**Requisitos:** o Service Worker só funciona em **HTTPS** ou em `localhost`/`127.0.0.1`. Para instalar em celulares da rede, publique o sistema com HTTPS (por exemplo, atrás de um proxy com certificado).

| Funciona sem internet | Exige internet |
|---|---|
| Abrir o aplicativo instalado e a página "Modo offline" | Login, cadastro e recuperação de senha |
| Consultar o rebanho, os lotes, a agenda sanitária e as tarefas **já baixados** neste aparelho | Painel, financeiro e relatórios |
| Registrar **pesagens**, **vacinas e tratamentos** e **tarefas** na fila local | Marketplace, profissionais, vagas, chat, feed e grupos |
| Ver as pendências e o estado de cada envio | Clima (mostra o último dado obtido com a data) e cotações |

Como funciona:

- Arquivos estáticos ficam em cache versionado. Páginas privadas **nunca** são guardadas em cache: sem conexão, aparece uma página offline genérica.
- Os dados ficam no IndexedDB, em um banco separado por usuário (identificador opaco gerado pelo servidor). Não há senhas, tokens nem dados financeiros nesse banco.
- A cópia local é atualizada automaticamente a cada 30 minutos com o sistema aberto, ou pelo botão "Baixar dados agora".
- Cada registro offline recebe um UUID, e o servidor é idempotente: reenvios nunca duplicam. Um item só sai da fila depois que o servidor confirma a gravação.
- **Conflitos:** pesagem do mesmo animal na mesma data com peso diferente, aplicação repetida, ou animal vendido ou morto enquanto o aparelho estava offline. O usuário escolhe "Manter os dois registros" ou "Descartar".
- A sincronização confere se a sessão aberta pertence ao mesmo usuário dono da fila.
- **Ao sair da conta:** a cópia local dos dados é apagada. Registros ainda não enviados permanecem (com aviso antes de sair) e só podem ser enviados pela mesma conta.
- Os dados no aparelho não são criptografados pelo aplicativo. Proteja o aparelho com senha ou biometria.

## 8. Integrações externas

- **Clima:** Open-Meteo (gratuito, sem chave), com cache de 60 minutos no banco. Sem conexão, mostra o último dado com data e hora ou informa indisponibilidade. Para desativar: `WEATHER_ENABLED=false`.
- **Cotações:** não há fonte pública e gratuita confiável com API para arroba, leite e grãos. Por isso, as cotações são registradas pelo administrador (formulário ou importação CSV), sempre com fonte e data de referência. O sistema nunca gera valores.
- **E-mail:** configure SMTP no `.env`. Sem SMTP, em desenvolvimento os e-mails (recuperação de senha, confirmação) são gravados em `instance/mail_outbox/`. Em produção, nada é enviado de forma simulada.
- **Pagamentos e entregas:** não integrados. O marketplace informa isso claramente e a negociação é feita pelo chat.

## 9. Limitações conhecidas e próximos passos

- O chat usa atualização periódica (a cada 4 s), não WebSocket.
- Relatórios em PDF são gerados por "Imprimir › Salvar como PDF", com folha de estilo de impressão. Não há gerador de PDF no servidor.
- Notificações push e envio de e-mail de alertas não foram implementados: os alertas são internos.
- O modo offline cobre pesagens, sanidade e tarefas. Cadastro de animais e financeiro offline não foram incluídos, para evitar conflitos de identificação e exposição de dados financeiros no aparelho.
- SQLite atende bem a uma instalação local ou a poucos usuários simultâneos. Para muitos acessos, migre para PostgreSQL (`DATABASE_URL`). O comando `backup` cobre apenas SQLite.
- A política de privacidade e os termos de uso são textos-base e devem ser revisados juridicamente antes da operação comercial.
