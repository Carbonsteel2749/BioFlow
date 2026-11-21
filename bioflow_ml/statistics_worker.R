args <- commandArgs(trailingOnly=TRUE)
cfg <- as.list(read.dcf(args[1])[1,])
out <- cfg$output
read_input <- function(path) read.table(path,header=TRUE,sep=if(grepl("\\.tsv$",path)) "\t" else ",",row.names=1,check.names=FALSE,stringsAsFactors=FALSE,colClasses="character")
x <- read_input(cfg$table);x[]<-lapply(x,as.numeric);meta <- read_input(cfg$metadata);meta <- meta[rownames(x),,drop=FALSE]
split_names <- function(value) if(is.null(value)||!nzchar(value)) character() else trimws(strsplit(value,",",fixed=TRUE)[[1]])
group <- cfg$group_column;subject <- cfg$subject_column
meta[[group]] <- relevel(factor(meta[[group]]),ref=cfg$reference)
num <- split_names(cfg$numeric_covariates);cat <- split_names(cfg$categorical_covariates)
if(nzchar(cfg$batch_column)) cat <- c(cat,cfg$batch_column)
if(nzchar(cfg$time_column) && !cfg$time_column %in% c(group,num,cat) && cfg$node %in% c("vegan_permanova","ancombc2","maaslin2")) cat<-c(cat,cfg$time_column)
for(name in num) meta[[name]] <- as.numeric(meta[[name]])
for(name in cat) meta[[name]] <- factor(meta[[name]])
set.seed(as.integer(cfg$seed))
result <- NULL
if(cfg$node %in% c("ancombc2","maaslin2")) {
  # Stable valid internal names prevent R packages from silently rewriting numeric IDs.
  feature_ids<-setNames(colnames(x),paste0("bf_feature_",seq_len(ncol(x))))
  colnames(x)<-names(feature_ids)
  write.csv(data.frame(internal_id=names(feature_ids),feature_id=unname(feature_ids)),file.path(out,"feature-id-map.csv"),row.names=FALSE)
  original_id<-function(ids) {value<-unname(feature_ids[as.character(ids)]);if(anyNA(value))stop("Statistics package returned an unmapped feature ID");value}
}
if(cfg$node %in% c("vegan_permanova","vegan_betadisper")) {
  suppressPackageStartupMessages(library(vegan))
  distance <- as.dist(as.matrix(x))
  permutation <- as.integer(cfg$permutations)
  if(cfg$design!="independent") permutation <- permute::how(nperm=permutation,blocks=factor(meta[[subject]]))
  if(cfg$node=="vegan_permanova") {
    terms <- c(if(cfg$design!="independent") subject,group,num,cat)
    formula <- reformulate(terms,response="distance")
    model <- adonis2(formula,data=meta,permutations=permutation,by="margin",parallel=1)
    result <- data.frame(term=rownames(model),model,check.names=FALSE)
    result$q_value <- p.adjust(result[["Pr(>F)"]],method="BH")
    write.csv(result,file.path(out,"effects.csv"),row.names=FALSE)
    saveRDS(model,file.path(out,"vegan-model.rds"))
  } else {
    model <- betadisper(distance,meta[[group]],type="median",bias.adjust=TRUE)
    test <- permutest(model,permutations=permutation)
    write.csv(data.frame(term=rownames(test$tab),test$tab,check.names=FALSE),file.path(out,"effects.csv"),row.names=FALSE)
    write.csv(data.frame(sample_id=names(model$distances),distance_to_group_median=model$distances,group=meta[[group]]),file.path(out,"dispersion-distances.csv"),row.names=FALSE)
  }
} else if(cfg$node=="ancombc2") {
  suppressPackageStartupMessages(library(ANCOMBC))
  if(cfg$design!="independent") meta[[subject]] <- factor(meta[[subject]])
  # Keep a true data.frame even for one selected metadata column in package internals.
  ancom_meta <- data.frame(sample_id=rownames(meta),meta,check.names=FALSE,row.names=rownames(meta))
  output <- ancombc2(data=t(as.matrix(x)),meta_data=ancom_meta,fix_formula=paste(c(group,num,cat),collapse=" + "),
    rand_formula=if(cfg$design!="independent") paste0("(1|",subject,")") else NULL,
    group=group,p_adj_method="BH",alpha=as.numeric(cfg$fdr_alpha),prv_cut=as.numeric(cfg$min_prevalence),lib_cut=0,n_cl=2,global=FALSE,pairwise=FALSE,verbose=FALSE)
  native <- output$res;write.csv(native,file.path(out,"ancombc2-native.csv"),row.names=FALSE)
  parts <- list()
  for(column in grep("^lfc_",names(native),value=TRUE)) {
    term <- sub("^lfc_","",column);se <- native[[paste0("se_",term)]]
    parts[[term]] <- data.frame(feature=original_id(native$taxon),term=term,effect=native[[column]],ci_lower=native[[column]]-qnorm(.975)*se,ci_upper=native[[column]]+qnorm(.975)*se,p_value=native[[paste0("p_",term)]],q_value=native[[paste0("q_",term)]],unit="ANCOM-BC2 native log-fold-change",method="ANCOM-BC2; Wald 95% CI")
  }
  result <- do.call(rbind,parts);write.csv(result,file.path(out,"effects.csv"),row.names=FALSE)
} else if(cfg$node=="maaslin2") {
  suppressPackageStartupMessages(library(Maaslin2))
  if(cfg$unit=="relative_percent") x <- x/100
  normalization <- if(cfg$unit %in% c("relative_fraction","relative_percent")) "NONE" else "TSS"
  model <- Maaslin2(input_data=x,input_metadata=meta,output=file.path(out,"maaslin2"),fixed_effects=c(group,num,cat),
    random_effects=if(cfg$design!="independent") subject else NULL,reference=paste(group,cfg$reference,sep=","),
    normalization=normalization,transform="LOG",analysis_method="LM",min_prevalence=as.numeric(cfg$min_prevalence),max_significance=as.numeric(cfg$fdr_alpha),correction="BH",standardize=FALSE,plot_heatmap=FALSE,plot_scatter=FALSE,cores=2)
  native <- read.delim(file.path(out,"maaslin2/all_results.tsv"),check.names=FALSE)
  result <- data.frame(feature=original_id(native$feature),term=paste(native$metadata,native$value,sep="="),effect=native$coef,ci_lower=native$coef-qnorm(.975)*native$stderr,ci_upper=native$coef+qnorm(.975)*native$stderr,p_value=native$pval,q_value=native$qval,unit="MaAsLin2 LOG-model native coefficient",method="MaAsLin2 LM; approximate Wald 95% CI")
  write.csv(result,file.path(out,"effects.csv"),row.names=FALSE)
} else stop("Unknown statistics method")
capture.output(sessionInfo(),file=file.path(out,"R-session.txt"))
