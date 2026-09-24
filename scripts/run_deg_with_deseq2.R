#!/usr/bin/env Rscript

# BioFlow DESeq2 差异分析脚本
# 使用 DESeq2 包进行 RNA-seq 原始 counts 数据的差异表达分析

args <- commandArgs(trailingOnly = TRUE)
if (length(args) < 3) {
  cat("Usage: Rscript run_deg_with_deseq2.R <input_counts> <metadata_file> <output_prefix>\n")
  quit(status = 1)
}

input_counts <- args[1]
metadata_file <- args[2]
output_prefix <- args[3]
log2fc_threshold <- ifelse(length(args) >= 4, as.numeric(args[4]), 1.0)
padj_threshold <- ifelse(length(args) >= 5, as.numeric(args[5]), 0.05)

# 加载必要的包
if (!require("DESeq2")) {
  cat("Installing DESeq2...\n")
  if (!require("BiocManager")) install.packages("BiocManager")
  BiocManager::install("DESeq2", update = FALSE, ask = FALSE)
  library(DESeq2)
}
if (!require("tibble")) {
  install.packages("tibble", dependencies = FALSE)
  library(tibble)
}
library(DESeq2)
library(tibble)

# 读取 counts 矩阵（行为基因，列为样本）
cat("Reading counts matrix...\n")
count_matrix <- read.csv(input_counts, row.names = 1, check.names = FALSE)
count_matrix <- as.matrix(count_matrix)

# 确保 counts 是整数
count_matrix <- round(count_matrix)

# 读取 metadata
cat("Reading metadata...\n")
metadata <- read.csv(metadata_file, stringsAsFactors = FALSE)
rownames(metadata) <- metadata[, 1]
metadata <- metadata[, -1, drop = FALSE]

# 确保样本顺序一致
common_samples <- intersect(colnames(count_matrix), rownames(metadata))
if (length(common_samples) == 0) {
  stop("No common samples between counts matrix and metadata")
}
count_matrix <- count_matrix[, common_samples]
metadata <- metadata[common_samples, , drop = FALSE]

# 获取分组信息
group <- factor(metadata$group)
design <- model.matrix(~ 0 + group)
colnames(design) <- levels(group)

# 使用 DESeq2 进行差异分析
cat("Creating DESeq2 object...\n")
dds <- DESeqDataSetFromMatrix(
  countData = count_matrix,
  colData = metadata,
  design = design
)

cat("Running DESeq2 analysis...\n")
dds <- DESeq(dds)

# 构建对比
contrast_name <- paste(levels(group)[2], levels(group)[1], sep = "_vs_")
cat(sprintf("Extracting results for %s...\n", contrast_name))
res <- results(
  dds,
  contrast = c("group", levels(group)[2], levels(group)[1]),
  lfcThreshold = log2fc_threshold,
  alpha = padj_threshold
)

# 按 padj 排序
res <- res[order(res$padj), ]

# 添加显著标记
res_df <- as.data.frame(res)
res_df <- rownames_to_column(res_df, "gene_id")
res_df$significant <- !is.na(res_df$padj) & abs(res_df$log2FoldChange) >= log2fc_threshold & res_df$padj <= padj_threshold

# 统计上调/下调基因
n_up <- sum(res_df$significant & res_df$log2FoldChange > 0, na.rm = TRUE)
n_down <- sum(res_df$significant & res_df$log2FoldChange < 0, na.rm = TRUE)

cat(sprintf("DEG analysis completed: %d significant genes (%d up, %d down)\n", 
            n_up + n_down, n_up, n_down))

# 输出 DEG 结果
deg_file <- paste0(output_prefix, "_deg_results.csv")
write.csv(res_df, deg_file, row.names = FALSE, na = "")
cat(sprintf("DEG results written to: %s\n", deg_file))

# 输出火山图数据
volcano_data <- res_df[, c("gene_id", "log2FoldChange", "pvalue", "padj", "significant")]
volcano_data$neg_log10_padj <- -log10(volcano_data$padj)
volcano_file <- paste0(output_prefix, "_volcano.csv")
write.csv(volcano_data, volcano_file, row.names = FALSE, na = "")
cat(sprintf("Volcano data written to: %s\n", volcano_file))

# 输出归一化后的表达矩阵（可选）
rld <- rlog(dds, blind = FALSE)
normalized_counts <- assay(rld)
normalized_file <- paste0(output_prefix, "_normalized_counts.csv")
write.csv(normalized_counts, normalized_file)
cat(sprintf("Normalized counts written to: %s\n", normalized_file))

# 输出汇总JSON
summary_json <- sprintf('{
  "deg_total_genes": %d,
  "deg_significant_total": %d,
  "deg_up_regulated": %d,
  "deg_down_regulated": %d,
  "deg_log2fc_threshold": %.2f,
  "deg_padj_threshold": %.2f,
  "groups": ["%s", "%s"],
  "group_sizes": {"%s": %d, "%s": %d},
  "method": "DESeq2"
}',
nrow(res_df),
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