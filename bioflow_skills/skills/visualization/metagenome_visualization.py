"""Contracts for traceable descriptive metagenome visualizations.

This is a BioFlow-native adaptation inspired by the public GPTomics/bioSkills
``metagenome-visualization`` skill. It does not copy upstream code or
instructions. It specifies the checks and handoffs that a result adapter and
renderer must complete before a figure is accepted.
"""

from __future__ import annotations

from typing import Any

try:  # Standard package import
    from ...core import BaseSkill, SkillInput, SkillValidationError, SkillSpec
    from ...registry import register_skill
except ImportError:  # Compatibility with legacy top-level ``skills`` imports
    from core import BaseSkill, SkillInput, SkillValidationError, SkillSpec
    from registry import register_skill


REFERENCE_URL = (
    "https://github.com/GPTomics/bioSkills/blob/main/metagenomics/"
    "metagenome-visualization/SKILL.md"
)

_PROFILE_RULES: dict[str, dict[str, Any]] = {
    "metaphlan": {
        "measurement": "relative_abundance",
        "feature_scope": "taxonomic profile",
        "display_views": ("composition_stacked_bar", "top_features_bar", "heatmap"),
        "warning": (
            "MetaPhlAn values are relative abundances. Preserve the reported unit and do not "
            "apply count-only operations."
        ),
    },
    "bracken": {
        "measurement": "read_count",
        "feature_scope": "taxonomic profile",
        "display_views": ("composition_stacked_bar", "top_features_bar", "heatmap"),
        "warning": (
            "Bracken values are read counts. Preserve the count table; any relative-abundance "
            "conversion is display-only and must be recorded in the figure manifest."
        ),
    },
    "humann": {
        "measurement": "functional_abundance",
        "feature_scope": "gene family, KO, EC, or pathway profile",
        "display_views": ("top_features_bar", "heatmap"),
        "warning": (
            "HUMAnN functional features must retain their declared feature type and "
            "normalization. Do not label genes, KOs, ECs, or pathways as taxa."
        ),
    },
}

_TABLE_RULES: dict[str, dict[str, Any]] = {
    "species_abundance": {
        "profiles": ("metaphlan", "bracken"),
        "description": "Taxonomic abundance table used for composition and taxon heatmaps.",
    },
    "gene_families": {
        "profiles": ("humann",),
        "description": "Gene-family table; total and species-stratified values must be distinguished.",
    },
    "pathway_abundance": {
        "profiles": ("humann",),
        "description": "Pathway-abundance table with declared normalization.",
    },
    "ko_abundance": {
        "profiles": ("humann",),
        "description": "KO abundance table; keep functional identifiers intact.",
    },
    "ec_abundance": {
        "profiles": ("humann",),
        "description": "EC abundance table; keep functional identifiers intact.",
    },
    "pathway_coverage": {
        "profiles": ("humann",),
        "description": "Pathway coverage is not an abundance table and needs a dedicated view.",
        "descriptive_views": (),
    },
}

_VIEW_RULES: dict[str, dict[str, Any]] = {
    "composition_stacked_bar": {
        "purpose": "Show dominant taxonomic composition for each sample.",
        "requires": ("sample_id", "feature_name", "abundance"),
        "rules": (
            "Aggregate low-abundance features into an explicitly labelled Other category.",
            "Keep sample order explicit and retain the source abundance unit in the axis label.",
        ),
    },
    "top_features_bar": {
        "purpose": "Show the most abundant taxa or functional features in the selected samples.",
        "requires": ("feature_name", "abundance"),
        "rules": (
            "State whether abundance is per sample, mean abundance, or total abundance.",
            "Use a deterministic top-N rule and record N in the figure manifest and caption.",
        ),
    },
    "heatmap": {
        "purpose": "Compare feature patterns across multiple samples.",
        "requires": ("sample_id", "feature_name", "abundance"),
        "rules": (
            "Select features with a documented rule before plotting.",
            "State every row scaling or transformation in the manifest and caption.",
        ),
    },
}


def _normalise_profile_type(value: object) -> str:
    profile_type = str(value).strip().lower()
    aliases = {
        "metaphlan_relative_abundance": "metaphlan",
        "bracken_counts": "bracken",
        "humann_gene_families": "humann",
        "humann_pathways": "humann",
    }
    return aliases.get(profile_type, profile_type)


@register_skill
class MetagenomeVisualizationSkill(BaseSkill):
    """Plan first-stage figures and the adapter/Artifact contracts they require.

    The skill intentionally does not open a result directory or generate a
    figure. The owning analysis service resolves ``task_id``, validates the
    actual table, executes rendering, and registers the returned Artifact.
    """

    spec = SkillSpec(
        name="metagenome-visualization",
        version="0.2.0",
        category="visualization",
        description=(
            "为 BioLLM 宏基因组真实结果定义可追溯的基础作图、数据适配和 Artifact "
            "交付规范；适用于 MetaPhlAn、Bracken 与 HUMAnN。"
        ),
        tags=(
            "metagenomics", "microbiome", "visualization", "artifact-contract",
            "data-adapter", "no-group-required", "gptomics-reference",
        ),
        input_keys=(
            "task_context", "task_id", "profile_type", "table_role", "abundance_table",
        ),
        output_keys=(
            "data_adapter_contract", "visualization_plan", "figure_specifications",
            "artifact_contract",
        ),
    )

    def validate_input(self, skill_input: SkillInput) -> None:
        super().validate_input(skill_input)
        for key in self.spec.input_keys:
            value = skill_input.get(key)
            if not isinstance(value, str) or not value.strip():
                raise SkillValidationError(f"{key} must be a non-empty string")

        profile_type = _normalise_profile_type(skill_input.get("profile_type"))
        if profile_type not in _PROFILE_RULES:
            supported = ", ".join(sorted(_PROFILE_RULES))
            raise SkillValidationError(
                f"unsupported profile_type '{profile_type}'; supported: {supported}"
            )

        table_role = str(skill_input.get("table_role")).strip().lower()
        if table_role not in _TABLE_RULES:
            supported = ", ".join(sorted(_TABLE_RULES))
            raise SkillValidationError(
                f"unsupported table_role '{table_role}'; supported: {supported}"
            )
        if profile_type not in _TABLE_RULES[table_role]["profiles"]:
            raise SkillValidationError(
                f"table_role '{table_role}' is not compatible with profile_type '{profile_type}'"
            )

        requested_views = skill_input.get("requested_views")
        if requested_views is not None:
            if not isinstance(requested_views, list) or not all(
                isinstance(view, str) for view in requested_views
            ):
                raise SkillValidationError("requested_views must be a list of strings")
            unknown = sorted(set(requested_views) - set(_VIEW_RULES))
            if unknown:
                raise SkillValidationError(
                    f"unsupported requested_views: {', '.join(unknown)}"
                )

        metadata_table = skill_input.get("sample_metadata_table")
        if metadata_table is not None and (
            not isinstance(metadata_table, str) or not metadata_table.strip()
        ):
            raise SkillValidationError("sample_metadata_table must be a non-empty string when set")

    def run(self, skill_input: SkillInput):
        profile_type = _normalise_profile_type(skill_input.get("profile_type"))
        table_role = str(skill_input.get("table_role")).strip().lower()
        profile_rule = _PROFILE_RULES[profile_type]
        table_rule = _TABLE_RULES[table_role]
        supported_views = tuple(table_rule.get("descriptive_views", profile_rule["display_views"]))
        if not supported_views:
            return self.failure(
                "selected result table is not an abundance table supported by this skill",
                data={"profile_type": profile_type, "table_role": table_role},
                warnings=[
                    "Pathway coverage cannot be treated as pathway abundance; it needs a "
                    "separate coverage-specific visualization contract."
                ],
            )
        selected_views = tuple(skill_input.get("requested_views") or supported_views)
        disallowed = sorted(set(selected_views) - set(supported_views))
        if disallowed:
            return self.failure(
                "requested views are incompatible with the selected result table",
                data={
                    "profile_type": profile_type,
                    "table_role": table_role,
                    "requested_views": list(selected_views),
                    "available_views": list(supported_views),
                },
                warnings=[
                    "Pathway coverage cannot be treated as pathway abundance; it requires a "
                    "separate coverage-specific visualization contract."
                ],
            )

        figure_specs = []
        for view in selected_views:
            rule = _VIEW_RULES[view]
            figure_specs.append(
                {
                    "name": view,
                    "purpose": rule["purpose"],
                    "required_canonical_fields": list(rule["requires"]),
                    "rules": list(rule["rules"]),
                    "required_outputs": ["png", "svg", "pdf", "figure_data", "figure_manifest"],
                }
            )

        sample_metadata = skill_input.get("sample_metadata_table")
        warnings = [profile_rule["warning"]]
        if not sample_metadata:
            warnings.append(
                "Only descriptive figures are allowed. Group comparison, differential testing, "
                "and clinical association require an externally supplied sample metadata table."
            )
        else:
            warnings.append(
                "Sample metadata does not itself authorize group comparison: validate sample IDs "
                "and select its statistical method in a later comparison skill."
            )

        data_adapter_contract = {
            "source_resolution": {
                "task_id": skill_input.get("task_id"),
                "preferred_source": "existing task result tables or result API",
                "archive_policy": (
                    "Do not use a delivery archive containing FASTQ files as normal server-side "
                    "rendering input; use it only for delivery and archival."
                ),
                "requested_table": {
                    "role": table_role,
                    "reference": skill_input.get("abundance_table"),
                    "description": table_rule["description"],
                },
            },
            "required_validation": [
                "Read the actual header and map it to canonical fields; never assume a fixed upstream column order.",
                "Validate every plotted sample_id against the task sample list and, if supplied, the metadata table.",
                "Obtain unit and normalization from table metadata, column labels, or producer provenance; never infer them from numeric magnitude.",
                "Preserve missing values and report them. Do not silently convert missing values to zero.",
                "For gene families, distinguish total values from species-stratified rows before aggregation.",
                "Handle KO, EC, pathway abundance, and pathway coverage as separate table roles; coverage is not abundance.",
            ],
            "canonical_output": {
                "required_fields": ["sample_id", "feature_id", "feature_name", "value", "unit"],
                "required_metadata": [
                    "table_role", "measurement_type", "normalization", "source_file",
                    "source_checksum_or_artifact_id", "missing_value_policy",
                ],
                "forbidden_inference": [
                    "numeric-magnitude unit inference", "missing-to-zero coercion",
                    "group inference from abundance values",
                ],
            },
        }

        visualization_plan = {
            "execution_mode": "analysis_service_renderer_handoff",
            "rendering_status": "not_rendered_by_this_skill",
            "input_contract": {
                "task_id": skill_input.get("task_id"),
                "profile_type": profile_type,
                "measurement": profile_rule["measurement"],
                "feature_scope": profile_rule["feature_scope"],
                "table_role": table_role,
                "sample_metadata_table": sample_metadata,
            },
            "workflow": [
                "Resolve the current task output by task_id and validate the actual source table.",
                "Emit a canonical plotting dataset plus validation report before calling a renderer.",
                "Generate selected descriptive figures with deterministic selection and transformation rules.",
                "Register figures and provenance in the task result center, then expose them to the web/API and archive process.",
            ],
            "quality_checks": [
                "Axes, legends, feature labels, sample scope, and abundance units are unambiguous.",
                "Each figure records source table, source artifact, sample range, aggregation, transformation, and software parameters.",
                "Every figure is reproducible from retained plotting data and manifest, not only from its PNG.",
            ],
        }

        artifact_contract = {
            "task_id": skill_input.get("task_id"),
            "register_per_figure": {
                "artifact_type": "figure.metagenome.<view_name>",
                "files": ["png", "svg", "pdf", "figure_data", "figure_manifest"],
                "metadata": [
                    "task_id", "source_artifact_id_or_checksum", "source_table", "table_role",
                    "unit", "normalization", "sample_scope", "method", "parameters",
                ],
            },
            "delivery_requirements": [
                "Expose registered figures through existing Artifact list and download/result APIs.",
                "Show registered figures in the task web view rather than leaving them in a private directory.",
                "Include figures and manifests in task archival output; re-rendering after archival must refresh the archive.",
            ],
        }

        return self.success(
            data={
                "data_adapter_contract": data_adapter_contract,
                "visualization_plan": visualization_plan,
                "figure_specifications": figure_specs,
                "artifact_contract": artifact_contract,
            },
            summary=(
                f"已为任务 {skill_input.get('task_id')} 的 {table_role} 定义数据适配、"
                f"{len(figure_specs)} 项基础作图与 Artifact 交付契约；尚未实际读取或绘图。"
            ),
            warnings=warnings,
            metadata={
                "reference": REFERENCE_URL,
                "adapted": True,
                "stage": "descriptive_visualization_contract",
                "requires_group_metadata_for_comparison": True,
                "requires_phylogeny_for_unifrac": True,
            },
        )
