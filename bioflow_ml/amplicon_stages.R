cfg <- as.list(read.dcf(commandArgs(TRUE)[1])[1,]);suppressPackageStartupMessages(library(dada2))
out <- cfg$output;threads <- as.integer(cfg$threads);set.seed(as.integer(cfg$seed))
write_track <- function(value) write.csv(value,file.path(out,"read_tracking.csv"),row.names=FALSE)
getN <- function(x) sum(getUniques(x))
if(cfg$node %in% c("amplicon_filter","amplicon_errors","amplicon_denoise")) {
  samples<-read.csv(cfg$manifest,stringsAsFactors=FALSE,colClasses="character");stopifnot(!anyDuplicated(samples$sample_id))
}
if(cfg$node=="amplicon_filter") {
  dir.create(file.path(out,"filtered"));f<-file.path(out,"filtered",paste0(samples$sample_id,".R1.fastq.gz"));r<-file.path(out,"filtered",paste0(samples$sample_id,".R2.fastq.gz"))
  track<-filterAndTrim(samples$read1,f,samples$read2,r,truncLen=c(as.integer(cfg$trunc_len_forward),as.integer(cfg$trunc_len_reverse)),maxN=0,maxEE=c(as.numeric(cfg$max_ee),as.numeric(cfg$max_ee)),truncQ=2,rm.phix=TRUE,multithread=threads)
  write_track(data.frame(sample_id=samples$sample_id,track,check.names=FALSE))
  if(any(track[,"reads.out"]==0)) stop("A sample has no filtered reads; explicitly resolve exclusions before continuing")
  write.csv(data.frame(sample_id=samples$sample_id,read1=f,read2=r),file.path(out,"manifest.csv"),row.names=FALSE)
} else if(cfg$node=="amplicon_errors") {
  f<-learnErrors(samples$read1,multithread=threads,randomize=TRUE);r<-learnErrors(samples$read2,multithread=threads,randomize=TRUE)
  saveRDS(getErrors(f),file.path(out,"forward_error.rds"));saveRDS(getErrors(r),file.path(out,"reverse_error.rds"))
} else if(cfg$node=="amplicon_denoise") {
  ef<-readRDS(cfg$forward_error);er<-readRDS(cfg$reverse_error)
  for(value in list(ef,er)) if(!is.matrix(value)||!is.numeric(value)||nrow(value)!=16||any(!is.finite(value))||any(value<0)||any(value>1)) stop("Invalid DADA2 error matrix")
  df<-derepFastq(samples$read1);dr<-derepFastq(samples$read2);names(df)<-names(dr)<-samples$sample_id
  f<-dada(df,err=ef,multithread=threads);r<-dada(dr,err=er,multithread=threads)
  saveRDS(list(derepF=df,derepR=dr,dadaF=f,dadaR=r),file.path(out,"denoised.rds"))
  write_track(data.frame(sample_id=samples$sample_id,denoisedF=sapply(f,getN),denoisedR=sapply(r,getN)))
} else if(cfg$node=="amplicon_merge") {
  state<-readRDS(cfg$denoised)
  stopifnot(identical(names(state$dadaF),names(state$dadaR)),identical(names(state$dadaF),names(state$derepF)))
  pairs<-mergePairs(state$dadaF,state$derepF,state$dadaR,state$derepR,minOverlap=as.integer(cfg$min_overlap),verbose=TRUE)
  table<-makeSequenceTable(pairs);if(ncol(table)==0) stop("No merged ASVs; check read overlap")
  lengths<-nchar(colnames(table));write.csv(data.frame(length=lengths,reads=colSums(table)),file.path(out,"region-lengths.csv"),row.names=FALSE)
  if(any(lengths<as.integer(cfg$min_amplicon_length)|lengths>as.integer(cfg$max_amplicon_length))) stop("Merged ASV length is outside the declared region bounds; review region-lengths.csv instead of silently discarding sequences")
  if(any(rowSums(table)==0)) stop("A sample has no merged reads; resolve exclusions explicitly")
  saveRDS(table,file.path(out,"sequence_table.rds"));write_track(data.frame(sample_id=names(pairs),merged=sapply(pairs,getN)))
} else if(cfg$node=="amplicon_chimera") {
  table<-readRDS(cfg$sequence_table)
  if(!is.matrix(table)||any(!is.finite(table))||any(table<0)||any(table%%1!=0)||any(!grepl("^[ACGT]+$",colnames(table)))) stop("Invalid sequence table")
  clean<-removeBimeraDenovo(table,method="consensus",multithread=threads,verbose=TRUE);if(ncol(clean)==0) stop("No ASVs after chimera filtering")
  if(any(rowSums(clean)==0)) stop("A sample has no non-chimeric reads; resolve exclusions explicitly")
  seqs<-colnames(clean);ids<-paste0("ASV",seq_along(seqs));colnames(clean)<-ids
  write.csv(data.frame(sample_id=rownames(clean),clean,check.names=FALSE),file.path(out,"asv_table.csv"),row.names=FALSE)
  con<-file(file.path(out,"asv_sequences.fasta"),"wt");for(i in seq_along(ids))writeLines(c(paste0(">",ids[i]),seqs[i]),con);close(con)
  write_track(data.frame(sample_id=rownames(clean),merged=rowSums(table),nonchim=rowSums(clean)))
} else stop("Unknown DADA2 stage")
capture.output(sessionInfo(),file=file.path(out,"R-session.txt"))
