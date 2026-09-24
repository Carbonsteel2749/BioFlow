#!/usr/bin/env Rscript

# BioFlow 差异分析 R 脚本
# 使用 limma 包进行差异表达分析（芯片数据/已归一化数据标准流程）

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  cat("Usage: Rscript run_deg_with_r.R <input_matrix> <metadata_file> <output_prefix>\n")
  quit(status = 1)
}

input_matrix <- args[1]
metadata_file <- args[2]
output_prefix <- args[3]
log2fc_threshold <- ifelse(length(args) >= 4, as.numeric(args[4]), 1.0)
padj_threshold <- ifelse(length(args) >= 5, as.numeric(args[5]), 0.05)

# 加载必要的包
if (!require("limma")) {
  cat("Installing limma...\n")
  BiocManager::install("limma", update = FALSE, ask = FALSE)
  library(limma)
}
if (!require("edgeR")) {
  cat("Installing edgeR...\n")
  BiocManager::install("edgeR", update = FALSE, ask = FALSE)
  library(edgeR)
}
library(BiocManager)
library(limma)
library(edgeR)
library(tibble)

# 读取表达矩阵（行为基因，列为样本）
cat("Reading expression matrix...\n")
expr_matrix <- read.csv(input_matrix, row.names = 1, check.names = FALSE)
expr_matrix <- as.matrix(expr_matrix)

# 读取 metadata
cat("Reading metadata...\n")
metadata <- read.csv(metadata_file, stringsAsFactors = FALSE)
rownames(metadata) <- metadata[, 1]
metadata <- metadata[, -1, drop = FALSE]

# 确保样本顺序一致
common_samples <- intersect(colnames(expr_matrix), rownames(metadata))
if (length(common_samples) == 0) {
  stop("No common samples between matrix and metadata")
}
expr_matrix <- expr_matrix[, common_samples]
metadata <- metadata[common_samples, , drop = FALSE]

# 获取分组信息
group <- factor(metadata$group)
design <- model.matrix(~ 0 + group)
colnames(design) <- levels(group)

# 使用 limma 进行差异分析
cat("Running limma analysis...\n")
fit <- lmFit(expr_matrix, design)
contrasts <- makeContrasts(
  contrasts = paste(levels(group)[2], levels(group)[1], sep = "-"),
  levels = design
)
fit2 <- contrasts.fit(fit, contrasts)
fit2 <- eBayes(fit2)

# 提取结果
de_results <- topTable(fit2, coef = 1, adjust = "fdr", number = Inf, sort.by = "P")

# 添加显著标记
de_results <- rownames_to_column(as.data.frame(de_results), "gene_id")
de_results$significant <- abs(de_results$logFC) >= log2fc_threshold & de_results$adj.P.Val <= padj_threshold

# 统计上调/下调基因
n_up <- sum(de_results$significant & de_results$logFC > 0)
n_down <- sum(de_results$significant & de_results$logFC < 0)

cat(sprintf("DEG analysis completed: %d significant genes (%d up, %d down)\n", 
            n_up + n_down, n_up, n_down))

# 输出 DEG 结果
deg_file <- paste0(output_prefix, "_deg_results.csv")
write.csv(de_results, deg_file, row.names = FALSE)
cat(sprintf("DEG results written to: %s\n", deg_file))

# 输出火山图数据
volcano_data <- de_results[, c("gene_id", "logFC", "P.Value", "adj.P.Val", "significant")]
colnames(volcano_data) <- c("gene_id", "log2FoldChange", "pvalue", "padj", "significant")
volcano_data$neg_log10_padj <- -log10(volcano_data$padj)
volcano_file <- paste0(output_prefix, "_volcano.csv")
write.csv(volcano_data, volcano_file, row.names = FALSE)
cat(sprintf("Volcano data written to: %s\n", volcano_file))

# 输出汇总JSON
summary_json <- sprintf('{
  "deg_total_genes": %d,
  "deg_significant_total": %d,
  "deg_up_regulated": %d,
  "deg_down_regulated": %d,
  "deg_log2fc_threshold": %.2f,
  "deg_padj_threshold": %.2f,
  "groups": ["%s", "%s"],
  "group_sizes": {"%s": %d, "%s": %d}
}',
nrow(de_results),
n_up + n_down,
n_up,
n_down,
log2fc_threshold,
padj_threshold,
levels(group)[1], levels(group)[2],
levels(group)[1], sum(group == levels(group)[1]),
levels(group)[2], sum(group == levels(group)[2])
)
summary_file <- paste0(output_prefix, "_summary.json")
writeLines(summary_json, summary_file)
cat(sprintf("Summary written to: %s\n", summary_file))