import sys
import os

current_dir = os.path.dirname(os.path.abspath(__file__))

project_root = os.path.dirname(current_dir) 

nested_package_path = os.path.join(project_root, "article_writing", "article_writing")

if os.path.isdir(nested_package_path):
    sys.path.insert(0, os.path.join(project_root, "article_writing"))
else:
    sys.path.insert(0, project_root)

from article_writing.contracts import SectionInput, SectionDraft, SectionId, AnalysisBundle, PaperBrief, LiteratureHit
from article_writing.sections.conclusion import ConclusionSection

MOCK_ANALYSIS = {
    "summary": "After QC and normalization, differential expression identified 128 significant genes (72 up, 56 down) between group A (cSCC) and group B (NHEK).",
    "methods": {
        "qc": "low-detection and low-mean gene filters; outlier samples removed by correlation",
        "normalization": "log1p on pre-normalized microarray-like matrix",
        "differential_analysis": {
            "method": "welch_t_with_bh",
            "group_a": "cSCC",
            "group_b": "NHEK",
            "log2fc_threshold": 1.0,
            "padj_threshold": 0.05
        }
    },
    "key_findings": [
        "128 genes passed |log2FC| >= 1 and FDR < 0.05",
        "Top up-regulated marker gene_SET_A shows log2FC=2.4",
        "Pathway-level enrichment is not yet available in this fixture"
    ],
    "metrics": {
        "n_genes_tested": 12000,
        "n_significant": 128,
        "n_up": 72,
        "n_down": 56,
        "agreement_score_vs_literature": 0.62
    },
    "limitations": [
        "Enrichment analysis not included in Phase-1 fixture",
        "Sample size is modest; effect sizes should be interpreted cautiously"
    ],
    "figure_refs": [
        {
            "figure_id": "fig_pca",
            "path": "fixtures/figures/pca_placeholder.txt",
            "caption": "PCA of samples after normalization",
            "kind": "figure"
        },
        {
            "figure_id": "tbl_deg",
            "path": "fixtures/figures/deg_top_placeholder.txt",
            "caption": "Top differentially expressed genes",
            "kind": "table"
        }
    ],
    "evidence_ids": [
        "ev_deg_summary",
        "ev_metric_n_sig",
        "ev_fig_pca"
    ],
    "raw": {
        "source": "fixture",
        "note": "Replace via AnalysisPort live adapter when plate 4 is ready"
    }
}

MOCK_LITERATURE = [
    {
        "paper_id": "lit_001",
        "title": "Gut microbiota alterations in autism spectrum disorder: a systematic review",
        "abstract": "This review summarizes microbiome composition changes reported in ASD cohorts and discusses immune and metabolic pathways.",
        "authors": "Smith A; Lee B",
        "year": 2022,
        "doi": "10.1000/fixture.asd.microbiome.001",
        "pmid": "10000001",
        "score": 0.91,
        "tags": ["autism", "gut microbiota", "review"]
    },
    {
        "paper_id": "lit_002",
        "title": "Keratinocyte inflammatory programs in cutaneous squamous cell carcinoma",
        "abstract": "Transcriptomic profiling of cSCC keratinocytes highlights inflammatory and epithelial remodeling signatures.",
        "authors": "Chen C; Patel D",
        "year": 2021,
        "doi": "10.1000/fixture.cscc.002",
        "pmid": "10000002",
        "score": 0.88,
        "tags": ["cSCC", "keratinocyte", "transcriptome"]
    },
    {
        "paper_id": "lit_003",
        "title": "Short-chain fatty acids mediate gut-skin axis signaling",
        "abstract": "SCFAs modulate epithelial barrier and inflammatory tone, providing a mechanistic bridge between gut microbiota and skin phenotypes.",
        "authors": "Nguyen E; Rossi F",
        "year": 2020,
        "doi": "10.1000/fixture.scfa.003",
        "pmid": "10000003",
        "score": 0.76,
        "tags": ["SCFA", "gut-skin axis", "inflammation"]
    },
    {
        "paper_id": "lit_004",
        "title": "Best practices for differential expression analysis of bulk RNA-seq",
        "abstract": "Guidelines covering normalization, multiple-testing correction, and reporting of DEG thresholds.",
        "authors": "Kim G; Alvarez H",
        "year": 2019,
        "doi": "10.1000/fixture.deg.methods.004",
        "pmid": "10000004",
        "score": 0.7,
        "tags": ["DEG", "methods", "RNA-seq"]
    },
    {
        "paper_id": "lit_005",
        "title": "Immune-microbiome crosstalk in epithelial barrier disorders",
        "abstract": "Host immune tone and microbial metabolites jointly shape barrier integrity across gut and skin epithelia, with implications for inflammatory disease models.",
        "authors": "Brown I; Zhao J",
        "year": 2023,
        "doi": "10.1000/fixture.barrier.005",
        "pmid": "10000005",
        "score": 0.82,
        "tags": ["immune", "microbiome", "epithelial barrier", "autism"]
    }
]

MOCK_PAPER_BRIEF = {
    "title": "Differential expression of cutaneous squamous cell carcinoma cell lines versus normal keratinocytes",
    "research_question": "Which genes are differentially expressed between cSCC cells and NHEKs, and how do these findings relate to prior microbiome-immune literature?",
    "organism_or_system": "human keratinocytes / cSCC cell lines",
    "data_modality": "expression_matrix",
    "keywords": [
        "cutaneous squamous cell carcinoma",
        "differential expression",
        "keratinocyte",
        "autism",
        "gut microbiota"
    ],
    "notes": [
        "Fixture brief for Phase-1 parallel development.",
        "Literature keywords intentionally mix oncology DEG and ASD-microbiome themes for adapter demos."
    ]
}


def test_conclusion_generation():
    section_input = SectionInput(
        section_id=SectionId.CONCLUSION,
        analysis_bundle=AnalysisBundle(**MOCK_ANALYSIS),
        paper_brief=PaperBrief(**MOCK_PAPER_BRIEF),
        literature_hits=[LiteratureHit(**lit) for lit in MOCK_LITERATURE]
    )

    generator = ConclusionSection()
    draft = generator.generate(section_input)

    assert isinstance(draft, SectionDraft)
    assert draft.section_id == SectionId.CONCLUSION
    assert len(draft.content) > 0
    
    content_lower = draft.content.lower()
    assert "cscc" in content_lower or "squamous" in content_lower
    assert "128" in draft.content or "significant" in content_lower