# 📡 Radar de Licitações para MEI / ME / EPP (protótipo)

Encontra licitações **abertas** na região de São José dos Pinhais / Curitiba, mostra quais são **boas para pequenos**, explica cada uma em **linguagem simples** e lista **empresas MEI, ME e EPP** que provavelmente se interessam.

## Como usar

| Arquivo | O que faz |
|---|---|
| `RODAR-RAPIDO.bat` | Teste rápido, só São José dos Pinhais (~2 min) |
| `RODAR-RADAR.bat` | Região completa (SJP, Curitiba, Pinhais, Araucária, Colombo...) (~10 a 20 min) |
| `guia-licitacao-simples.html` | Guia para MEI: "Licitação sem complicação" (dá pra imprimir) |

É só dar **dois cliques**. Não precisa instalar nada (usa o PowerShell que já vem no Windows).

Os resultados vão para a pasta `saida/`:
- `relatorio_*.html`: relatório visual com filtros e botão "copiar mensagem pro WhatsApp"
- `oportunidades_*.csv`: planilha das licitações (abre no Excel)
- `empresas-interessadas_*.csv`: planilha de empresas MEI/ME/EPP com contato
- `mensagens-whatsapp_*.txt`: mensagens prontas para enviar

## Ligar a IA (opcional, recomendado)

Sem IA, o radar já gera um resumo automático simples. **Com IA**, ele lê o PDF do edital e explica também **quais documentos pedem** e **os cuidados**.

1. Pegue uma chave grátis em https://aistudio.google.com/apikey
2. Crie um arquivo chamado `chave-gemini.txt` nesta pasta e cole a chave dentro
3. Rode de novo

## Configurar

No começo do arquivo `radar.ps1`, na seção **CONFIGURAÇÃO**, dá pra mudar:
- as cidades (código IBGE)
- quantos dias pra frente olhar
- quantas empresas consultar, quantos resumos de IA etc.

## Como funciona

1. **PNCP** (Portal Nacional de Contratações Públicas): licitações com proposta aberta em cada cidade
2. **Itens** de cada licitação: identifica itens **exclusivos ME/EPP**, **cota reservada**, valor até R$ 80 mil e compra direta (dispensa)
3. **Nota** de 0 a ~8: ✅ ótima / 🟡 pode valer / ⚪ difícil para pequenos
4. **Empresas**: pega os contratos dos últimos 180 dias desses órgãos e consulta cada fornecedor na Receita (via BrasilAPI). Ficam **só MEI, ME e EPP ativas**, cruzadas com as licitações abertas parecidas com o que já venderam.

## Limitações do protótipo

- O cruzamento empresa × licitação é por **palavras em comum**: funciona bem, mas não é perfeito.
- O PNCP limita o número de consultas por minuto; o radar espera sozinho quando isso acontece.
- Empresas que **nunca venderam** pro governo não aparecem (próximo passo: base completa de CNPJs da Receita por atividade/CNAE).
- **Sempre confira o edital oficial.** Resumos automáticos podem ter erros.

## ⚖️ Cuidados

- **LGPD:** dados de empresa são públicos, mas dados de MEI se confundem com dados da pessoa. Use para contato com finalidade legítima e respeite quem pedir para não receber mensagens.
- **Conflito de interesse:** se você trabalha no setor público, confirme com o RH/jurídico o que é permitido antes de usar isso comercialmente.
