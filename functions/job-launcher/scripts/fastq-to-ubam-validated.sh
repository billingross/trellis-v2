FASTQ_R1_FILES_PATH="$(dirname "${FASTQ_R1}")"
FASTQ_R1_FILES_PATTERN="$(basename "${FASTQ_R1}")"
FASTQ_R1_FILE_LIST=( $(ls "${FASTQ_R1_FILES_PATH}"/${FASTQ_R1_FILES_PATTERN}) )
FIRST_FASTQ_R1=${FASTQ_R1_FILE_LIST[0]}

FASTQ_R2_FILES_PATH="$(dirname "${FASTQ_R2}")"
FASTQ_R2_FILES_PATTERN="$(basename "${FASTQ_R2}")"
FASTQ_R2_FILE_LIST=( $(ls "${FASTQ_R2_FILES_PATH}"/${FASTQ_R2_FILES_PATTERN}) )
FIRST_FASTQ_R2=${FASTQ_R2_FILE_LIST[0]}

/gatk/gatk --java-options
    \\'-Xmx8G -Djava.io.tmpdir=bla\\'
    FastqToSam
    -F1 ${FIRST_FASTQ_R1}
    -F2 ${FIRST_FASTQ_R2}
    -O ${UBAM}
    -RG ${RG}
    -SM ${SM}
    -PL ${PL}
