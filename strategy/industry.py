"""行业分类器。"""
class IndustryClassifier:
    def classify(self, code):
        if code.startswith('6'): return '沪市主板'
        if code.startswith('000') or code.startswith('001'): return '深市主板'
        if code.startswith('300') or code.startswith('301'): return '创业板'
        if code.startswith('688'): return '科创板'
        return '其他'
