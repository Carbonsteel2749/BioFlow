args <- commandArgs(trailingOnly=TRUE)
cfg <- as.list(read.dcf(args[1])[1, ])
suppressPackageStartupMessages(library(dada2))
out <- cfg$output
dir.create(out,recursive=TRUE,showWarnings=FALSE)
set.seed(as.integer(cfg$seed))
threads <- as.integer(cfg$threads)
if(cfg$node == "amplicon_asv") {
  samples <- read.csv(cfg$manifest,stringsAsFactors=FALSE,check.names=FALSE)
  if(anyDuplicated(samples$sample_id) || nrow(samples)<1) stop("Sample identifiers must be unique and non-empty")
  filtered <- file.path(out,"filtered")
  dir.create(filtered,showWarnings=FALSE)
  fwd <- file.path(filtered,paste0(samples$sample_id,"_F.fastq.gz"))
  rev <- file.path(filtered,paste0(samples$sample_id,"_R.fastq.gz"))
  track <- filterAndTrim(samples$read1,fwd,samples$read2,rev,
    truncLen=c(as.integer(cfg$trunc_len_forward),as.integer(cfg$trunc_len_reverse)),
    maxN=0,maxEE=c(as.numeric(cfg$max_ee),as.numeric(cfg$max_ee)),truncQ=2,
    rm.phix=TRUE,compress=TRUE,multithread=threads)
  keep <- track[,"reads.out"]>0
  if(!any(keep)) stop("No reads passed filtering; no ASVs were inferred")
  samples <- samples[keep,,drop=FALSE];fwd <- fwd[keep];rev <- rev[keep];track <- track[keep,,drop=FALSE]
  errF <- learnErrors(fwd,multithread=threads,randomize=TRUE)
  errR <- learnErrors(rev,multithread=threads,randomize=TRUE)
  derepF <- derepFastq(fwd);derepR <- derepFastq(rev)
  names(derepF) <- samples$sample_id;names(derepR) <- samples$sample_id
  dadaF <- dada(derepF,err=errF,multithread=threads)
  dadaR <- dada(derepR,err=errR,multithread=threads)
  merged <- mergePairs(dadaF,derepF,dadaR,derepR,minOverlap=as.integer(cfg$min_overlap),verbose=TRUE)
  seqtab <- makeSequenceTable(merged)
  if(ncol(seqtab)==0) stop("No paired sequences merged; check overlap and trimming")
  nonchim <- removeBimeraDenovo(seqtab,method="consensus",multithread=threads,verbose=TRUE)
  if(ncol(nonchim)==0) stop("No non-chimeric ASVs remain")
  sequences <- colnames(nonchim);ids <- paste0("ASV",seq_along(sequences));colnames(nonchim) <- ids
  write.csv(data.frame(sample_id=rownames(nonchim),nonchim,check.names=FALSE),file.path(out,"asv_table.csv"),row.names=FALSE)
  con <- file(file.path(out,"asv_sequences.fasta"),"wt")
  for(i in seq_along(ids)) writeLines(c(paste0(">",ids[i]),sequences[i]),con)
  close(con)
  getN <- function(x) sum(getUniques(x))
  tracking <- data.frame(sample_id=samples$sample_id,input=track[,"reads.in"],filtered=track[,"reads.out"],
    denoisedF=sapply(dadaF,getN),denoisedR=sapply(dadaR,getN),merged=sapply(merged,getN),nonchim=rowSums(nonchim))
  write.csv(tracking,file.path(out,"read_tracking.csv"),row.names=FALSE)
  saveRDS(list(errorF=errF,errorR=errR),file.path(out,"learned_errors.rds"))
} else if(cfg$node == "amplicon_taxonomy") {
  fasta <- readLines(cfg$asv_sequences)
  header <- startsWith(fasta,">")
  if(!any(header)) stop("ASV FASTA is empty")
  groups <- cumsum(header)
  # Iterate numeric FASTA order, rather than lexicographic split labels (1,10,2).
  seqs <- vapply(seq_len(sum(header)),function(i) paste0(fasta[!header & groups==i],collapse=""),FUN.VALUE=character(1))
  ids <- sub("^>","",fasta[header])
  if(length(seqs)!=length(ids)) stop("Invalid ASV FASTA")
  tax <- assignTaxonomy(seqs,cfg$training_fasta,minBoot=as.integer(cfg$min_boot),multithread=threads)
  write.csv(data.frame(asv_id=ids,tax,check.names=FALSE),file.path(out,"asv_taxonomy.csv"),row.names=FALSE)
} else stop("Unknown amplicon node")
capture.output(sessionInfo(),file=file.path(out,"dada2-session.txt"))
