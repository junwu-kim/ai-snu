import os
import sys
import re

# --- 라이브러리 체크 ---
try:
    import pymupdf4llm
    from langchain_text_splitters import MarkdownHeaderTextSplitter, RecursiveCharacterTextSplitter
    from langchain_community.vectorstores import FAISS
    from langchain_community.chat_models import ChatOllama
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_core.prompts import ChatPromptTemplate
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.runnables import RunnablePassthrough
except ImportError as e:
    print(f"라이브러리 로드 에러: {e}")
    sys.exit(1)

# ==========================================
# 설정
# ==========================================
INPUT_PDF = "stm32_rtc_extract.pdf"
PROCESSED_MD = "stm32_context_cache.md"
MODEL_NAME = "llama3"

# 저장할 파일명
BASELINE_FILE = "baseline_rtc_driver.c" # 1. 일반 LLM이 짠 코드 (틀린거)
AGENT_FILE = "agent_rtc_driver.c"       # 2. 에이전트가 고친 코드 (맞는거)

# ==========================================
# 유틸리티 함수
# ==========================================
def extract_code_block(text):
    """
    LLM 출력에서 여러 개의 ```c 블록이 있을 경우, 
    가장 길이가 긴 블록(전체 코드일 확률이 높음)을 추출합니다.
    """
    pattern = r"```c(.*?)```"
    match = re.search(pattern, text, re.DOTALL)
    if match:
        return match.group(1).strip()
    return text # 코드 블록 없으면 전체 반환

def save_to_file(content, filename):
    with open(filename, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[System] 파일 저장 완료: {filename}")

# ==========================================
# 데이터 처리 및 에이전트 초기화
# ==========================================
def load_and_process_data(pdf_path, md_path):
    if not os.path.exists(md_path):
        print(f"[System] PDF 변환 시작: {pdf_path}")
        md_text = pymupdf4llm.to_markdown(pdf_path)
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(md_text)
    
    with open(md_path, "r", encoding="utf-8") as f:
        full_text = f.read()

    headers_to_split_on = [("#", "Header 1"), ("##", "Header 2"), ("###", "Header 3")]
    markdown_splitter = MarkdownHeaderTextSplitter(headers_to_split_on=headers_to_split_on)
    md_header_splits = markdown_splitter.split_text(full_text)
    text_splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=200)
    return text_splitter.split_documents(md_header_splits)

def initialize_agent(splits):
    print("[System] 벡터 DB 구축 중...")
    embedding_model = HuggingFaceEmbeddings(model_name="sentence-transformers/all-MiniLM-L6-v2")
    vectorstore = FAISS.from_documents(documents=splits, embedding=embedding_model)
    retriever = vectorstore.as_retriever(search_kwargs={"k": 6})
    llm = ChatOllama(model=MODEL_NAME, temperature=0)
    return retriever, llm

# ==========================================
# 메인 로직
# ==========================================
def main():
    splits = load_and_process_data(INPUT_PDF, PROCESSED_MD)
    retriever, llm = initialize_agent(splits)

    # ---------------------------------------------------------
    # 1. Baseline Generation (Vanilla RAG)
    # ---------------------------------------------------------
    # 에이전트 없이 그냥 문서만 보고 짜라고 시킴 -> 보통 PWR을 빼먹음
    coder_template = """
    당신은 C 언어 개발자입니다. 
    제공된 문서를 참고하여 STM32F103 RTC 초기화(LSE 사용) 코드를 작성하세요.
    
    [지침]
    1. 레지스터 직접 제어 방식을 사용하세요.
    2. 코드는 ```c 블록 안에 작성하세요.

    [Context]
    {context}

    [User Request]
    {question}
    """
    coder_prompt = ChatPromptTemplate.from_template(coder_template)

    def format_docs(docs):
        return "\n\n".join(doc.page_content for doc in docs)

    coder_chain = (
        {"context": retriever | format_docs, "question": RunnablePassthrough()}
        | coder_prompt
        | llm
        | StrOutputParser()
    )

    question = "STM32F103에서 LSE(외부 저속 클럭)를 사용하여 RTC를 초기화하고 1초마다 인터럽트를 발생시키는 코드를 작성해줘."
    
    print(f"\n==========================================")
    print(f"[Phase 1] Baseline 코드 생성 중... (Verifier 없음)")
    baseline_response = coder_chain.invoke(question)
    
    # Baseline 코드 저장
    baseline_code = extract_code_block(baseline_response)
    save_to_file(baseline_code, BASELINE_FILE)
    print(f"-> 결과: {BASELINE_FILE} 생성됨 (아마 틀린 코드일 것임)")


    # ---------------------------------------------------------
    # 2. Agent Generation (Verification Loop)
    # ---------------------------------------------------------
    # 위에서 만든 코드를 검증기에 넣어서 수정하게 만듦
    print(f"\n==========================================")
    print(f"[Phase 2] Agent 검증 및 수정 중... (Self-Correction)")
    
    verifier_template = """
    당신은 '임베디드 코드 리뷰어'입니다. 
    앞서 작성된 코드(Generated Code)가 매뉴얼(Context)의 **치명적인 제약조건**을 지켰는지 검사하세요.
    
    [필수 점검 항목]
    1. **Pre-condition**: RTC에 접근하기 위해 `PWR` 및 `BKP` 클럭을 켰는가? (`RCC_APB1ENR`)
    2. **Write Protection**: `PWR_CR` 레지스터의 `DBP` 비트를 1로 설정했는가? (이게 없으면 동작 안 함!)
    
    만약 위 조건이 누락되었다면, **해당 설정을 추가하여 완벽하게 수정한 C 코드**를 다시 작성하세요.
    문제 없으면 "PASS"라고만 답하세요.

    [Reference Manual]
    {context}
    [Generated Code]
    {code}
    """
    verifier_prompt = ChatPromptTemplate.from_template(verifier_template)

    verifier_chain = (
        verifier_prompt
        | llm
        | StrOutputParser()
    )

    # 문맥 다시 검색
    docs = retriever.invoke(question)
    context_text = format_docs(docs)

    agent_response = verifier_chain.invoke({
        "context": context_text,
        "code": baseline_response # 위에서 만든 틀린 코드를 입력으로 줌
    })

    print("\n[Verifier Report]")
    print(agent_response)

    # 수정된 코드가 있으면 저장, 없으면 PASS라고 간주하고 기존 코드 저장(하지만 수정할 것임)
    if "```c" in agent_response:
        final_code = extract_code_block(agent_response)
        save_to_file(final_code, AGENT_FILE)
        print(f"-> 결과: {AGENT_FILE} 생성됨 (수정된 코드)")
    else:
        print("-> 수정사항 없음 (PASS). 기존 코드를 복사합니다.")
        save_to_file(baseline_code, AGENT_FILE)

    print("\n[System] 모든 작업 완료!")

if __name__ == "__main__":
    main()