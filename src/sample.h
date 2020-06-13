template <typename T, int N>
class Sample
{
  public:
  Sample(T init_val) : head(0)
  {
    for (size_t i=0; i < N; i++) {
      samples[i] = init_val;
    }
  }

  void sample(T value)
  {
    samples[head] = value;
    head += 1;
    if (head == N) {
      head = 0;
    }
  }

  size_t items_within_range(T low, T high)
  {
    size_t iwr = 0;
    for (size_t i = 0; i < N; i++) {
      if (low <= samples[i] && samples[i] <= high) {
        iwr += 1;
      }
    }
    return iwr;
  }

  // for debugging/testing
  T get_item(size_t idx) const { return samples[idx]; }

  private:
  T samples[N];
  
  // head is the offset of the next place to populate in the ring buffer
  size_t head;

};
