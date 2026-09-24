from bioflow_ml.bioinformatics import BrackenTool, FastpTool, KneadDataTool, Kraken2Tool


def test_fastp_builds_paired_end_command():
    result = FastpTool().run(dry_run=True, read1="r1.fq.gz", read2="r2.fq.gz", output1="clean_r1.fq.gz", output2="clean_r2.fq.gz", threads=8)
    assert result.status == "dry_run"
    assert result.command == ["fastp", "-i", "r1.fq.gz", "-o", "clean_r1.fq.gz", "-w", "8", "-I", "r2.fq.gz", "-O", "clean_r2.fq.gz"]


def test_kneaddata_builds_host_removal_command():
    command = KneadDataTool().build_command(read1="r1.fq.gz", read2="r2.fq.gz", output_dir="clean", reference_db="human_db", threads=6)
    assert command[:3] == ["kneaddata", "--input", "r1.fq.gz"]
    assert "human_db" in command


def test_kraken2_and_bracken_build_commands():
    kraken = Kraken2Tool().run(dry_run=True, database="db", read1="r1.fq.gz", report="sample.kreport", output="sample.kraken", confidence=0.1)
    bracken = BrackenTool().run(dry_run=True, database="db", report="sample.kreport", output="sample.bracken", read_length=150, level="S")
    assert kraken.command[0] == "kraken2"
    assert "--confidence" in kraken.command
    assert bracken.command == ["bracken", "-d", "db", "-i", "sample.kreport", "-o", "sample.bracken", "-r", "150", "-l", "S", "-t", "10"]
