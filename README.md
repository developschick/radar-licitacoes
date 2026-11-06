# Portal de Oportunidades Licitatórias — Fomento ao MEI, ME e EPP (PNCP)

> **PT-BR:** Sistema web e motor de triagem automatizada que consome a API oficial do **Portal Nacional de Contratações Públicas (PNCP)** e da **Receita Federal**, classifica editais abertos segundo os benefícios da **Lei Complementar nº 123/2006** e da **Lei nº 14.133/2021**, traduz o objeto técnico para linguagem acessível e cruza demandas públicas com micro e pequenas empresas locais em **São José dos Pinhais, Curitiba e Região Metropolitana (PR)**.
>
> **EN:** Automated public procurement intelligence portal built with Python (multi-threaded REST backend) and a Government Design System (DSGOV/e-MAG) frontend. Fetches live open tenders from Brazil's National Public Procurement Portal (PNCP), scores opportunities for Micro and Small Enterprises (LC 123/2006), translates legal/technical terms into plain language, and matches open tenders with local small suppliers via public CNPJ data.

---

## Principais Funcionalidades / Key Features

1. **Coleta Paralela de Alta Velocidade (`ThreadPoolExecutor`):**
   - Consulta editais abertos (`/api/consulta/v1/contratacoes/proposta`) e enriquece os itens e critérios de julgamento (`/api/pncp/v1/orgaos/{cnpj}/compras/{ano}/{seq}/itens`) em menos de **2 segundos** (redução de ~98% no tempo de resposta em comparação à coleta sequencial).
2. **Motor de Triagem e Pontuação (LC nº 123/2006 e Lei nº 14.133/2021):**
   - Identifica automaticamente itens exclusivos para MEI/ME/EPP (até R$ 80.000,00 — Art. 48, I da LC 123/2006), cotas reservadas (até 25%), Dispensas Eletrônicas e Atas de Registro de Preços (SRP).
3. **Classificação Precisa por Segmento Econômico (Regex Word-Boundary):**
   - Filtra processos em 10 setores econômicos (Alimentação, Limpeza, Obras/Engenharia, TI, Saúde/Laboratório, Frota/Peças, Expediente/Gráfica, Uniformes/EPI, Eventos e Agro) analisando estritamente o objeto de compra.
4. **Motor de Inteligência de CNAEs e Cruzamento de Fornecedores (Compras.gov.br + PNCP + Receita Federal):**
   - Integra a API de Dados Abertos do **Compras.gov.br** (`/modulo-fornecedor/1_consultarFornecedor`), o histórico de contratos do **PNCP** e a base de CNPJ da **Receita Federal**, mapeando mais de **135 fornecedores reais habilitados (`MEI`, `EPP` e `ME`)** na região.
   - Averigua individualmente o **CNAE Principal** e **todos os CNAEs Secundários** de cada empresa, descobrindo a vocação pública do CNPJ e gerando mensagens consultivas para WhatsApp com a licitação aberta mais compatível.
5. **Interface Institucional Padrão Governo (DSGOV / e-MAG) com Revelação Progressiva:**
   - Visual limpo e direto ao ponto, com painéis expansíveis sob demanda (`i Mais informações e explicação`) para evitar sobrecarga visual, barra de acessibilidade funcional com escalonamento dinâmico de tipografia (`A-`, `A°`, `A+` de 80% a 140% em `rem`) e modo de **Alto Contraste** (WCAG AAA).

---

## Estrutura do Projeto / Project Structure

```text
radar-licitacoes/
├── servidor_web.py            # Servidor HTTP multi-thread + API REST + Motor de CNAEs + PNCP/Compras.gov/BrasilAPI
├── site/
│   └── index.html             # Portal Institucional (5 abas: Editais, Fornecedores, Manual, Cartilha LC 123 e Institucional)
├── ABRIR-SITE.bat             # Inicializador rápido do servidor web no Windows
└── README.md                  # Documentação técnica (PT-BR / EN)
```

---

## Como Executar Localmente / How to Run

### Pré-requisitos
- **Python 3.10+** (utiliza apenas bibliotecas nativas da *Standard Library*: `http.server`, `urllib.request`, `concurrent.futures`, `json`, `re`). Nenhuma dependência externa (`pip`) é necessária.

### Passo a passo
1. Clone este repositório:
   ```bash
   git clone https://github.com/developschick/radar-licitacoes.git
   cd radar-licitacoes
   ```
2. Inicie o servidor web:
   ```bash
   python servidor_web.py
   ```
   *(No Windows, basta dar dois cliques em `ABRIR-SITE.bat` ou executar `py servidor_web.py`).*
3. Acesse no navegador:
   ```text
   http://localhost:8080
   ```

---

## Conformidade Legal e Dados Abertos / Legal Compliance

Este projeto é uma iniciativa autoral de estudo e inovação tecnológica em compras públicas e opera **100% sobre Dados Governamentais Abertos**:
- **Lei Federal nº 14.133/2021** (Nova Lei de Licitações e Contratos — Art. 174 PNCP);
- **Lei Complementar Federal nº 123/2006** (Estatuto Nacional da Microempresa e da Empresa de Pequeno Porte — Arts. 42 a 49);
- **Lei Complementar Estadual nº 163/2013 (PR)** e **Prejulgado nº 27 do TCE-PR**;
- **Decreto Municipal nº 5.807/2023** e **LC Municipal nº 55/2010** (São José dos Pinhais/PR);
- **Lei de Acesso à Informação (Lei nº 12.527/2011, Art. 8º)** e **LGPD (Lei nº 13.709/2018)**.
