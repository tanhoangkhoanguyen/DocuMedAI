class InstructionSupporter:
    def __init__(self):
        self.__instrunction_support_prompt = "Thank you for your detailed instruction. The response will be revived next time."

    def executor(self, config = None):
        return self.__instrunction_support_prompt