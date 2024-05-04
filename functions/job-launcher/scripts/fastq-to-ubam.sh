/gatk/gatk --java-options
    \\'-Xmx8G -Djava.io.tmpdir=bla\\'
    FastqToSam
    -F1 ${FASTQ_R1}
    -F2 ${FASTQ_R2}
    -O ${UBAM}
    -RG ${RG}
    -SM ${SM}
    -PL ${PL}
