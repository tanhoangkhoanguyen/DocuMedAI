// Labeled underline input used by the auth forms. Wraps a native <input> but
// exposes a simple value / onChange contract.
export default function Field({
    label,                // label
    value,                // current input value
    onChange,             // function to update value
    ...rest
  }: {
    label: string;
    value: string;
    onChange: (v: string) => void;
  } & Omit<React.InputHTMLAttributes<HTMLInputElement>, "value" | "onChange">) {        // Custom value, onChange
  return (
    <label className="block">
      <span className="mb-1 block font-mono text-[10.5px] uppercase tracking-[0.22em] text-[#102A26]/45">
        {label}
      </span>
      <input
        {...rest}
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="w-full border-0 border-b border-[#102A26]/20 bg-transparent pb-1 text-[15px] text-[#102A26] outline-none transition-colors placeholder:text-[#102A26]/30 focus:border-[#FF5436]"
      />
    </label>
  );
}
